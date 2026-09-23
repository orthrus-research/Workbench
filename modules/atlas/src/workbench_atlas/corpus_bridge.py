#!/usr/bin/env python3

"""Compose one exact Unified Process Atlas answer without merging authorities.

This is deliberately a narrow bridge, not a second knowledge store.  It
resolves one acceptance question and one typed runtime identity, verifies the
checked-in corpus links, and projects matching curated and quest records into a
single evidence-indexed answer.  Original records remain byte-for-byte
meaningful records of their own authority layer.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable, Sequence

import workbench_atlas.atlas_query as atlas_query
import workbench_atlas.atlas_continuation as atlas_continuation
import workbench_atlas.atlas_causal_projection as atlas_causal_projection
import workbench_atlas.knowledge_catalog as catalog
import workbench_atlas.infrastructure_projection as infrastructure_projection
import workbench_atlas.infrastructure_source_authorities as infrastructure_source_authorities
from workbench_atlas.runtime_graph import canonical_json, canonical_json_payload
from workbench_atlas.runtime_graph_chain_query import (
    ChainOptions,
    RuntimeGraphChainContinuation,
    build_process_chain,
    build_process_chain_from_targets,
)
from workbench_atlas.runtime_graph_domain_query import (
    CONSUMER_PREDICATES,
    GraphScope,
    MATERIAL_IDENTITY_ROLES,
    PRODUCER_PREDICATES,
    ProfileScope as DomainProfileScope,
    RuntimeGraphDomainQuery,
    ScopedTarget,
    alternative_semantics,
    find_consumers,
    find_producers,
)
from workbench_atlas.runtime_graph_query import (
    NodeSelector,
    PageRequest,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
    decode_record,
)
from workbench_atlas.runtime_graph_recycling_query import (
    RecyclingOptions,
    RecyclingRootSeed,
    RecyclingRouteTarget,
    RuntimeGraphRecyclingContinuation,
    build_recycling_paths,
    canonical_recycling_roots,
    validate_recycling_result,
)
import workbench_atlas.source_lock as source_lock

from workbench_atlas.layout import DATA_ROOT, LEXICON_SCHEMA_ROOT

DEFAULT_CATALOG_ROOT = DATA_ROOT / "catalogs"
DEFAULT_QUESTIONS = DATA_ROOT / "acceptance-questions-v1.json"
DEFAULT_PRODUCTION_ROOT = DATA_ROOT / "production"
QUESTION_SCHEMA = LEXICON_SCHEMA_ROOT / "corpus-question-v1.schema.json"
LINK_SCHEMA = LEXICON_SCHEMA_ROOT / "corpus-link-v1.schema.json"
QUEST_SCHEMA = LEXICON_SCHEMA_ROOT / "quest-graph-record-v1.schema.json"
ANSWER_SCHEMA = LEXICON_SCHEMA_ROOT / "corpus-answer-v1.schema.json"

ANSWER_FORMAT = "susy-unified-process-atlas-answer-v1"
ANSWER_SCHEMA_VERSION = 1

PROFILE_SIDES = {
    "COMMON_FINAL_STATE": {"CLIENT", "DEDICATED_SERVER"},
    "CLIENT_JEI_FINAL_STATE": {"CLIENT"},
    "OFFLINE_ARTIFACT_STATE": {"OFFLINE"},
}

# A question selector names a canonical entity kind.  This first bridge only
# enables kinds with one unambiguous primary runtime key.  Other key families
# remain available through Gate A and can be added without fuzzy fallback.
PRIMARY_RUNTIME_KEYS = {
    "material": "material-resource-location",
    "item": "item-resource-location",
    "fluid": "fluid-name",
    "ore-dictionary": "ore-dictionary-name",
    "ore-prefix": "ore-prefix-name",
    "recipe-map": "recipe-map-name",
    "machine": "machine-resource-location",
}

CATALOG_KINDS = {
    "material": "material",
    "ore-prefix": "ore_prefix",
    "recipe": "recipe",
    "recipe-map": "recipe_map",
    "machine": "machine",
}

SUPPORTED_REQUIRED_FIELDS = {
    "target",
    "runtime",
    "pinned_source",
    "declaration",
    "ownership",
    "quests",
    "quest_prerequisites",
    "evidence",
    "snapshot_id",
    "profile_scope",
    "known_gaps",
    "assertions",
    "producers",
    "consumers",
    "routes",
    "recipes",
    "executable_machines",
    "ingredient_slots",
    "reusable_requirements",
    "byproducts",
    "recycling_paths",
    "prerequisites",
    "machine_construction",
    "infrastructure_requirements",
    "classification",
    "confidence",
    "comparison",
}

QUEST_NODE_KINDS = {
    "quest_line",
    "quest",
    "task",
    "reward",
    "localization",
    "resource",
}
QUEST_EDGE_KINDS = {
    "belongs_to_line",
    "requires_quest",
    "has_task",
    "has_reward",
    "references_identity",
    "localized_by",
}


class CorpusBridgeError(ValueError):
    """Raised when an input or composed answer violates the bridge contract."""


@dataclass(frozen=True, order=True)
class Scope:
    profile: str
    physical_side: str

    @property
    def link_value(self) -> str:
        return f"{self.profile}/{self.physical_side}"

    def document(self, availability: str) -> dict[str, str]:
        return {
            "profile": self.profile,
            "physical_side": self.physical_side,
            "availability": availability,
        }


def causal_provenance_projection(
    result: dict[str, Any],
    *,
    snapshot_id: str,
    scopes: Iterable[Scope],
    runtime_node_ids: Iterable[str],
) -> dict[str, Any]:
    """Admit one B01 projection at the exact Atlas bridge boundary."""

    try:
        projection = atlas_causal_projection.project(result)
    except atlas_causal_projection.AtlasCausalProjectionError as exc:
        raise CorpusBridgeError(
            f"causal provenance projection failed closed: {exc}"
        ) from exc
    binding = projection["binding"]
    scope_pairs = {
        (scope.profile, scope.physical_side) for scope in scopes
    }
    binding_pair = (
        binding["scope"]["profile"],
        binding["scope"]["physical_side"],
    )
    if binding["snapshot_id"] != snapshot_id:
        raise CorpusBridgeError(
            "causal provenance belongs to a different snapshot"
        )
    if binding_pair not in scope_pairs:
        raise CorpusBridgeError(
            "causal provenance primary scope was not requested"
        )
    final_runtime_node_id = binding["final_runtime_record"][
        "runtime_node_id"
    ]
    if final_runtime_node_id not in set(runtime_node_ids):
        raise CorpusBridgeError(
            "causal provenance final runtime record differs from the "
            "resolved Atlas target"
        )
    return projection


PROFILE_COMPARISON_SCOPES = tuple(
    sorted(
        (
            Scope("COMMON_FINAL_STATE", "CLIENT"),
            Scope("COMMON_FINAL_STATE", "DEDICATED_SERVER"),
            Scope("CLIENT_JEI_FINAL_STATE", "CLIENT"),
            Scope("OFFLINE_ARTIFACT_STATE", "OFFLINE"),
        )
    )
)

COMPARISON_AUTHORITY_POLICY = {
    "mechanics": "scope-owned runtime-mechanics records only",
    "presentation": (
        "CLIENT_JEI_FINAL_STATE records joined by exact reconciles_to edges only"
    ),
    "missing_target": (
        "a missing exact target is unavailable state, not an empty observation set"
    ),
}


def _read_json(path: Path, label: str) -> Any:
    try:
        return catalog.read_json(path)
    except (OSError, catalog.CatalogError) as exc:
        raise CorpusBridgeError(f"cannot read {label} {path}: {exc}") from exc


def _schema(path: Path) -> dict[str, Any]:
    value = _read_json(path, "schema")
    if not isinstance(value, dict):
        raise CorpusBridgeError(f"schema is not an object: {path}")
    try:
        catalog._validate_schema_definition(value, path.name)
    except catalog.CatalogError as exc:
        raise CorpusBridgeError(str(exc)) from exc
    return value


def _validate_schema(
    value: Any,
    schema: dict[str, Any],
    label: str,
) -> None:
    try:
        catalog._validate_schema_value(value, schema, label)
    except catalog.CatalogError as exc:
        raise CorpusBridgeError(str(exc)) from exc


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        return catalog.read_jsonl(path)
    except (OSError, catalog.CatalogError) as exc:
        raise CorpusBridgeError(f"invalid {label} {path}: {exc}") from exc


def _load_question(path: Path, question_id: str) -> dict[str, Any]:
    value = _read_json(path, "acceptance questions")
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise CorpusBridgeError("acceptance questions must be a JSON array of objects")
    schema = _schema(QUESTION_SCHEMA)
    matches: list[dict[str, Any]] = []
    for index, row in enumerate(value, 1):
        _validate_schema(row, schema, f"{path.name}:{index}")
        if row["id"] == question_id:
            matches.append(row)
    if len(matches) != 1:
        raise CorpusBridgeError(
            f"question id must resolve exactly once: {question_id}; matches={len(matches)}"
        )
    question = matches[0]
    unsupported = sorted(
        field
        for field in question["required_fields"]
        if field.split(".", 1)[0] not in SUPPORTED_REQUIRED_FIELDS
    )
    if unsupported:
        raise CorpusBridgeError(
            "bounded corpus bridge does not implement required fields: "
            + ", ".join(unsupported)
        )
    return question


def _parse_scopes(values: Iterable[str]) -> tuple[Scope, ...]:
    result: list[Scope] = []
    for value in values:
        parts = value.split(":")
        if len(parts) != 2 or parts[1] not in PROFILE_SIDES.get(parts[0], set()):
            raise CorpusBridgeError(
                "scope must be a supported PROFILE:PHYSICAL_SIDE pair: "
                f"{value}"
            )
        result.append(Scope(parts[0], parts[1]))
    normalized = tuple(sorted(set(result)))
    if not normalized:
        raise CorpusBridgeError("at least one exact runtime scope is required")
    if len(normalized) != len(result):
        raise CorpusBridgeError("runtime scopes must not be duplicated")
    return normalized


def _load_catalogs(root: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        catalog.validate_catalogs(root)
        return catalog.load_catalogs(root)
    except (OSError, catalog.CatalogError) as exc:
        raise CorpusBridgeError(f"invalid curated catalog {root}: {exc}") from exc


def _optional_catalogs(
    root: Path | None,
    *,
    optional: bool,
) -> dict[str, list[dict[str, Any]]]:
    if optional and (root is None or not root.is_dir()):
        return {
            "entities.jsonl": [],
            "relations.jsonl": [],
            "claims.jsonl": [],
        }
    if root is None:
        raise CorpusBridgeError("curated catalog root is required")
    return _load_catalogs(root)


def _load_links(
    path: Path,
    snapshot_id: str,
    entities: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = _read_jsonl(path, "corpus links")
    schema = _schema(LINK_SCHEMA)
    identifiers: set[str] = set()
    for index, row in enumerate(rows, 1):
        _validate_schema(row, schema, f"{path.name}:{index}")
        if row["id"] in identifiers:
            raise CorpusBridgeError(
                f"corpus link identifiers are duplicated: {row['id']}"
            )
        identifiers.add(row["id"])
        if row["snapshot_id"] != snapshot_id:
            raise CorpusBridgeError(
                f"corpus link is scoped to a different snapshot: {row['id']}"
            )
        canonical = entities.get(row["canonical_entity_id"])
        if canonical is None:
            raise CorpusBridgeError(
                f"corpus link canonical entity is missing: {row['id']}"
            )
        if canonical.get("snapshot_id") != snapshot_id:
            raise CorpusBridgeError(
                f"corpus link canonical entity is scoped to a different snapshot: {row['id']}"
            )
        if row["basis"] != "exact-typed-key":
            raise CorpusBridgeError(
                "bounded corpus bridge does not accept reviewed overrides without "
                f"a separate review-evidence contract: {row['id']}"
            )
    return rows


def _optional_links(
    path: Path | None,
    snapshot_id: str,
    entities: dict[str, dict[str, Any]],
    *,
    optional: bool,
) -> list[dict[str, Any]]:
    if optional and (path is None or not path.is_file()):
        return []
    if path is None:
        raise CorpusBridgeError("corpus links path is required")
    return _load_links(path, snapshot_id, entities)


def _load_quest_graph(
    node_path: Path,
    edge_path: Path,
    snapshot_id: str,
) -> tuple[
    dict[str, dict[str, Any]],
    tuple[dict[str, Any], ...],
]:
    node_rows = _read_jsonl(node_path, "quest nodes")
    edge_rows = _read_jsonl(edge_path, "quest edges")
    schema = _schema(QUEST_SCHEMA)
    nodes: dict[str, dict[str, Any]] = {}
    record_ids: set[str] = set()
    for path, rows, record_type, kinds in (
        (node_path, node_rows, "node", QUEST_NODE_KINDS),
        (edge_path, edge_rows, "edge", QUEST_EDGE_KINDS),
    ):
        for index, row in enumerate(rows, 1):
            _validate_schema(row, schema, f"{path.name}:{index}")
            if row["record_type"] != record_type or row["kind"] not in kinds:
                raise CorpusBridgeError(
                    f"{path.name}:{index} is not a supported quest {record_type}"
                )
            if row["snapshot_id"] != snapshot_id:
                raise CorpusBridgeError(
                    f"quest record is scoped to a different snapshot: {row['id']}"
                )
            if row["id"] in record_ids:
                raise CorpusBridgeError(
                    f"quest record identifiers are duplicated: {row['id']}"
                )
            record_ids.add(row["id"])
            if record_type == "node":
                nodes[row["id"]] = row
    for row in edge_rows:
        if row["subject"] not in nodes or row["object"] not in nodes:
            raise CorpusBridgeError(
                f"quest edge endpoint is missing: {row['id']}"
            )
    return nodes, tuple(edge_rows)


def _optional_quest_graph(
    node_path: Path | None,
    edge_path: Path | None,
    snapshot_id: str,
    *,
    optional: bool,
) -> tuple[
    dict[str, dict[str, Any]],
    tuple[dict[str, Any], ...],
]:
    node_available = node_path is not None and node_path.is_file()
    edge_available = edge_path is not None and edge_path.is_file()
    if optional and not node_available and not edge_available:
        return {}, ()
    if node_path is None or edge_path is None:
        raise CorpusBridgeError(
            "quest authority requires both node and edge paths"
        )
    if optional and node_available != edge_available:
        raise CorpusBridgeError(
            "quest authority is partial; both node and edge paths are required"
        )
    return _load_quest_graph(
        node_path,
        edge_path,
        snapshot_id,
    )


def _record_scope(record: dict[str, Any]) -> Scope:
    scope = record.get("scope")
    if not isinstance(scope, dict):
        raise CorpusBridgeError(
            f"runtime record has no scope object: {record.get('id')}"
        )
    profile = scope.get("profile")
    physical_side = scope.get("physical_side")
    if (
        not isinstance(profile, str)
        or not isinstance(physical_side, str)
        or physical_side not in PROFILE_SIDES.get(profile, set())
    ):
        raise CorpusBridgeError(
            f"runtime record has an invalid scope: {record.get('id')}"
        )
    return Scope(profile, physical_side)


def _require_runtime_record(
    record: dict[str, Any],
    scope: Scope,
    snapshot_id: str,
) -> None:
    if _record_scope(record) != scope:
        raise CorpusBridgeError(
            f"runtime record escaped its exact scope: {record.get('id')}"
        )
    record_scope = record["scope"]
    if record_scope.get("snapshot_id") != snapshot_id:
        raise CorpusBridgeError(
            f"runtime record is scoped to a different snapshot: {record.get('id')}"
        )


def _adjacent_runtime(
    reader: RuntimeGraphReader,
    scope: Scope,
    node_id: str,
    predicate: str,
) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    """Return same-scope outgoing edge/node pairs in canonical order."""

    try:
        rows = reader.connection.execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.object "
            "WHERE edge.subject = ? AND edge.predicate = ? "
            "AND edge.profile = ? AND edge.physical_side = ? "
            "AND node.profile = ? AND node.physical_side = ? "
            "ORDER BY edge.id, node.id",
            (
                node_id,
                predicate,
                scope.profile,
                scope.physical_side,
                scope.profile,
                scope.physical_side,
            ),
        ).fetchall()
    except sqlite3.Error as exc:
        raise CorpusBridgeError(
            f"cannot traverse runtime identity evidence: {exc}"
        ) from exc
    return tuple(
        (
            decode_record(encoded_edge, "edge"),
            decode_record(encoded_node, "node"),
        )
        for encoded_edge, encoded_node in rows
    )


def _runtime_evidence_basis(record: dict[str, Any]) -> str:
    return (
        "runtime-presentation"
        if _record_scope(record).profile == "CLIENT_JEI_FINAL_STATE"
        else "runtime-mechanics"
    )


def _add_runtime_evidence(
    registry: dict[str, dict[str, Any]],
    record: dict[str, Any],
) -> str:
    entry = _evidence(
        record,
        basis=_runtime_evidence_basis(record),
        authority="normalized-runtime-graph",
        record_kind=(
            "runtime-edge"
            if record.get("record_type") == "edge"
            else "runtime-node"
        ),
    )
    _add_evidence(registry, entry)
    return str(entry["id"])


def _quest_runtime_basis(
    reader: RuntimeGraphReader,
    link: dict[str, Any],
    material_nodes: Sequence[dict[str, Any]],
    requested_scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prove an exact quest key reaches the selected material.

    The bridge never rewrites or namespace-guesses a quest key. A base
    item/fluid may reach its variant first, but every accepted path must end in
    an explicit, role-qualified ``has_material`` edge.
    """

    material_by_scope = {
        _record_scope(node): node
        for node in material_nodes
    }
    requested = set(requested_scopes)
    linked_scopes = {
        scope
        for scope in requested
        if scope.link_value in link["profile_scope"]
        and scope in material_by_scope
    }
    result: list[dict[str, Any]] = []
    for scope in sorted(linked_scopes):
        matches = reader.resolve_exact(
            NodeSelector.by_key(
                link["key_kind"],
                link["key_value"],
                profile=scope.profile,
                physical_side=scope.physical_side,
            )
        )
        if len(matches) != 1:
            state = "missing" if not matches else f"ambiguous ({len(matches)} matches)"
            raise CorpusBridgeError(
                "quest corpus link exact runtime key is "
                f"{state} in {scope.link_value}: {link['id']} "
                f"({link['key_kind']}={link['key_value']})"
            )
        observed = matches[0]
        target = material_by_scope[scope]
        _require_runtime_record(observed, scope, snapshot_id)
        _require_runtime_record(target, scope, snapshot_id)

        candidate_paths: list[
            tuple[
                tuple[dict[str, Any], ...],
                tuple[dict[str, Any], ...],
            ]
        ] = []
        for material_edge, material in _adjacent_runtime(
            reader,
            scope,
            str(observed["id"]),
            "has_material",
        ):
            attributes = material_edge.get("attributes")
            if (
                material["id"] == target["id"]
                and isinstance(attributes, dict)
                and attributes.get("role") in MATERIAL_IDENTITY_ROLES
            ):
                candidate_paths.append(
                    ((observed, material), (material_edge,))
                )

        if observed.get("kind") in {"item", "fluid"}:
            expected_variant_kind = (
                "item_variant"
                if observed["kind"] == "item"
                else "fluid_variant"
            )
            for variant_edge, variant in _adjacent_runtime(
                reader,
                scope,
                str(observed["id"]),
                "has_variant",
            ):
                if variant.get("kind") != expected_variant_kind:
                    continue
                for material_edge, material in _adjacent_runtime(
                    reader,
                    scope,
                    str(variant["id"]),
                    "has_material",
                ):
                    attributes = material_edge.get("attributes")
                    if (
                        material["id"] == target["id"]
                        and isinstance(attributes, dict)
                        and attributes.get("role") in MATERIAL_IDENTITY_ROLES
                    ):
                        candidate_paths.append(
                            (
                                (observed, variant, material),
                                (variant_edge, material_edge),
                            )
                        )

        paths: list[dict[str, Any]] = []
        path_keys: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        scope_evidence_ids: set[str] = set()
        for path_nodes, path_edges in candidate_paths:
            for record in (*path_nodes, *path_edges):
                _require_runtime_record(record, scope, snapshot_id)
            node_ids = tuple(str(record["id"]) for record in path_nodes)
            edge_ids = tuple(str(record["id"]) for record in path_edges)
            key = (node_ids, edge_ids)
            if key in path_keys:
                continue
            path_keys.add(key)
            identifiers = [
                _add_runtime_evidence(evidence, record)
                for record in (*path_nodes, *path_edges)
            ]
            scope_evidence_ids.update(identifiers)
            paths.append(
                {
                    "node_ids": list(node_ids),
                    "edge_ids": list(edge_ids),
                }
            )
        paths.sort(
            key=lambda row: (
                tuple(row["node_ids"]),
                tuple(row["edge_ids"]),
            )
        )
        if not paths:
            raise CorpusBridgeError(
                "quest corpus link has no explicit role-qualified has_material "
                f"path to {target['id']} in {scope.link_value}: {link['id']}"
            )
        result.append(
            {
                "scope": {
                    "profile": scope.profile,
                    "physical_side": scope.physical_side,
                },
                "selector": {
                    "key_kind": link["key_kind"],
                    "key_value": link["key_value"],
                },
                "resolved_node_id": observed["id"],
                "material_node_id": target["id"],
                "paths": paths,
                "evidence_ids": sorted(scope_evidence_ids),
            }
        )
    if not result:
        raise CorpusBridgeError(
            f"quest corpus link has no requested resolved runtime scope: {link['id']}"
        )
    return result


def _resolve_runtime(
    reader: RuntimeGraphReader,
    key_kind: str,
    key_value: str,
    runtime_kind: str,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[Scope, ...],
    bool,
]:
    resolved: list[dict[str, Any]] = []
    gaps: list[Scope] = []
    ambiguous = False
    for scope in scopes:
        matches = reader.resolve_exact(
            NodeSelector.by_key(
                key_kind,
                key_value,
                kind=runtime_kind,
                profile=scope.profile,
                physical_side=scope.physical_side,
            )
        )
        for node in matches:
            _require_runtime_record(node, scope, snapshot_id)
        if len(matches) > 1:
            ambiguous = True
            resolved.extend(matches)
            continue
        if not matches:
            gaps.append(scope)
            continue
        node = matches[0]
        resolved.append(node)
    return (
        tuple(
            sorted(
                resolved,
                key=lambda row: (
                    _record_scope(row),
                    str(row["id"]),
                ),
            )
        ),
        tuple(gaps),
        ambiguous,
    )


def _domain_graph_scope(scopes: tuple[Scope, ...]) -> GraphScope:
    return GraphScope.of(
        *(
            DomainProfileScope(scope.profile, scope.physical_side)
            for scope in scopes
        )
    )


def _runtime_records_in(value: Any) -> dict[str, dict[str, Any]]:
    """Collect every embedded runtime graph record without trusting ID lists."""

    records: dict[str, dict[str, Any]] = {}

    def visit(current: Any) -> None:
        if isinstance(current, dict):
            if current.get("record_type") in {"node", "edge"}:
                identifier = current.get("id")
                if not isinstance(identifier, str) or not identifier:
                    raise CorpusBridgeError(
                        "runtime projection contains a record without an id"
                    )
                previous = records.get(identifier)
                if previous is not None and previous != current:
                    raise CorpusBridgeError(
                        "runtime projection repeats an id with different records: "
                        + identifier
                    )
                records[identifier] = current
            for child in current.values():
                visit(child)
        elif isinstance(current, (list, tuple)):
            for child in current:
                visit(child)

    visit(value)
    return records


def _validated_runtime_projection_records(
    value: Any,
    *,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
) -> dict[str, dict[str, Any]]:
    """Validate requested scopes plus explicit HEI-to-runtime reconciliations."""

    requested = set(scopes)
    records = _runtime_records_in(value)
    record_scopes = {
        identifier: _record_scope(record)
        for identifier, record in records.items()
    }
    requested_ids = {
        identifier
        for identifier, scope in record_scopes.items()
        if scope in requested
    }
    reconciled_presentation_ids: set[str] = set()
    for identifier, record in records.items():
        if (
            record.get("record_type") != "edge"
            or record.get("predicate") != "reconciles_to"
            or record.get("object") not in requested_ids
        ):
            continue
        subject = record.get("subject")
        source = records.get(subject) if isinstance(subject, str) else None
        if (
            source is None
            or source.get("kind")
            not in {"recipe_wrapper", "recipe_example", "recipe_category"}
            or record_scopes[identifier] != record_scopes[str(subject)]
            or record_scopes[identifier].profile != "CLIENT_JEI_FINAL_STATE"
            or record_scopes[identifier].physical_side != "CLIENT"
        ):
            raise CorpusBridgeError(
                "runtime projection contains an invalid cross-profile "
                f"presentation reconciliation: {identifier}"
            )
        reconciled_presentation_ids.update((identifier, str(subject)))

    for identifier, record in sorted(records.items()):
        scope = record_scopes[identifier]
        if (
            scope not in requested
            and identifier not in reconciled_presentation_ids
        ):
            raise CorpusBridgeError(
                f"runtime projection escaped requested scopes: {identifier}"
            )
        _require_runtime_record(record, scope, snapshot_id)
    return records


def _add_runtime_projection_evidence(
    value: Any,
    *,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    records = _validated_runtime_projection_records(
        value,
        scopes=scopes,
        snapshot_id=snapshot_id,
    )
    identifiers: list[str] = []
    for identifier, record in sorted(records.items()):
        identifiers.append(_add_runtime_evidence(evidence, record))
    return tuple(identifiers)


def _usage_projection(
    reader: RuntimeGraphReader,
    *,
    direction: str,
    key_kind: str,
    key_value: str,
    runtime_kind: str,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
    page: PageRequest = PageRequest(limit=100, offset=0),
) -> dict[str, Any]:
    selector = NodeSelector.by_key(key_kind, key_value, kind=runtime_kind)
    graph_scope = _domain_graph_scope(scopes)
    if direction == "producers":
        result = find_producers(reader, selector, page, graph_scope).to_dict()
    elif direction == "consumers":
        result = find_consumers(reader, selector, page, graph_scope).to_dict()
    else:
        raise CorpusBridgeError(f"unsupported process-usage direction: {direction}")
    evidence_ids = _add_runtime_projection_evidence(
        result,
        scopes=scopes,
        snapshot_id=snapshot_id,
        evidence=evidence,
    )
    page_result = result["page"]
    return {
        "status": (
            "answered"
            if result["target"]["resolved"]
            else "unresolved"
        ),
        "target": result["target"],
        "items": page_result["items"],
        "page": {
            key: page_result[key]
            for key in ("limit", "offset", "returned", "total", "truncated")
        },
        "evidence_ids": list(evidence_ids),
    }


def _route_projection(
    reader: RuntimeGraphReader,
    *,
    key_kind: str,
    key_value: str,
    runtime_kind: str,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
    options: ChainOptions,
) -> dict[str, Any]:
    result = build_process_chain(
        reader,
        NodeSelector.by_key(key_kind, key_value, kind=runtime_kind),
        options=options,
        graph_scope=_domain_graph_scope(scopes),
    )
    evidence_ids = _add_runtime_projection_evidence(
        result,
        scopes=scopes,
        snapshot_id=snapshot_id,
        evidence=evidence,
    )
    resolved = bool(result["target"]["resolved"])
    routes = result["routes"]
    return {
        "status": (
            "answered"
            if routes
            else "no-mechanical-route"
            if resolved
            else "unresolved"
        ),
        "target": result["target"],
        "roots": result["roots"],
        "subproblems": result["subproblems"],
        "items": routes,
        "cycles": result["cycles"],
        "unresolved_leaves": result["unresolved_leaves"],
        "truncation": result["truncation"],
        "evidence_ids": list(evidence_ids),
    }


def _record_attributes(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    attributes = record.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _record_ordinal(record: Any, key: str) -> int:
    value = _record_attributes(record).get(key)
    return value if type(value) is int else 2**63 - 1


def _project_recipe_slot(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Retain one exact slot while making grouping and chance explicit."""

    relationship = row["relationship"]
    predicate = str(relationship.get("predicate", ""))
    alternatives = sorted(
        row["alternatives"],
        key=lambda alternative: (
            _record_ordinal(
                alternative["relationship"],
                "alternative_ordinal",
            ),
            str(alternative["node"].get("kind", "")),
            str(alternative["node"].get("id", "")),
            str(alternative["relationship"].get("id", "")),
        ),
    )
    classifications = {
        alternative_semantics(alternative["relationship"])
        for alternative in alternatives
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
    direction = (
        "output"
        if predicate in PRODUCER_PREDICATES
        else "input"
    )
    result = {
        "relationship": relationship,
        "slot": row["slot"],
        "semantics": {
            "direction": direction,
            "jointly_required": direction == "input",
            "reusable": predicate == "requires",
            "consumed": (
                direction == "input"
                and predicate != "requires"
            ),
            "conditional": predicate in {"may_consume", "may_produce"},
            "guaranteed": predicate not in {"may_consume", "may_produce"},
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
        },
    }
    chance: dict[str, Any] = {}
    for label, record in (
        ("relationship", relationship),
        ("slot", row["slot"]),
    ):
        attributes = {
            key: value
            for key, value in _record_attributes(record).items()
            if "chance" in key.lower() or "probab" in key.lower()
        }
        if attributes:
            chance[label] = attributes
    alternative_chances = [
        {
            "relationship_id": alternative["relationship"]["id"],
            "attributes": {
                key: value
                for key, value in _record_attributes(
                    alternative["relationship"]
                ).items()
                if "chance" in key.lower() or "probab" in key.lower()
            },
        }
        for alternative in alternatives
        if any(
            "chance" in key.lower() or "probab" in key.lower()
            for key in _record_attributes(alternative["relationship"])
        )
    ]
    if alternative_chances:
        chance["alternatives"] = alternative_chances
    result["chance"] = chance
    return result


def _projected_recipe_slot_order(
    row: dict[str, Any],
) -> tuple[int, str, str]:
    ordinal = _record_ordinal(row["slot"], "ordinal")
    if ordinal == 2**63 - 1:
        ordinal = _record_ordinal(row["relationship"], "ordinal")
    return (
        ordinal,
        str(row["relationship"].get("predicate", "")),
        str(row["slot"].get("id", "")),
    )


def _recipe_projection(
    reader: RuntimeGraphReader,
    *,
    producers: dict[str, Any],
    consumers: dict[str, Any],
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Union usage occurrences into one exact per-recipe detail projection."""

    grouped: dict[
        tuple[str, str, str],
        dict[str, Any],
    ] = {}
    non_recipe_occurrences: list[dict[str, Any]] = []
    for role, section in (
        ("producer", producers),
        ("consumer", consumers),
    ):
        for occurrence in section["items"]:
            owner = occurrence.get("owner")
            if not isinstance(owner, dict):
                raise CorpusBridgeError(
                    f"{role} occurrence has no owner record"
                )
            if owner.get("kind") != "recipe":
                non_recipe_occurrences.append(
                    {
                        "role": role,
                        "occurrence": occurrence,
                    }
                )
                continue
            scope = occurrence.get("scope")
            if not isinstance(scope, dict):
                raise CorpusBridgeError(
                    f"{role} recipe occurrence has no scope"
                )
            key = (
                str(scope.get("profile", "")),
                str(scope.get("physical_side", "")),
                str(owner.get("id", "")),
            )
            mechanics = occurrence.get("mechanics")
            current = grouped.get(key)
            if current is None:
                grouped[key] = {
                    "scope": scope,
                    "recipe": owner,
                    "roles": {role},
                    "mechanics": mechanics,
                    "occurrences": {
                        "producers": (
                            [occurrence] if role == "producer" else []
                        ),
                        "consumers": (
                            [occurrence] if role == "consumer" else []
                        ),
                    },
                }
                continue
            if (
                current["recipe"] != owner
                or current["scope"] != scope
                or current["mechanics"] != mechanics
            ):
                raise CorpusBridgeError(
                    "recipe occurrences disagree on exact owner mechanics: "
                    + str(owner.get("id"))
                )
            current["roles"].add(role)
            current["occurrences"][role + "s"].append(occurrence)

    items: list[dict[str, Any]] = []
    for key in sorted(grouped):
        current = grouped[key]
        recipe_id = str(current["recipe"]["id"])
        inputs = sorted(
            (
                _project_recipe_slot(row)
                for row in reader.recipe_slots(
                    recipe_id,
                    CONSUMER_PREDICATES,
                )
            ),
            key=_projected_recipe_slot_order,
        )
        consumed = [
            row
            for row in inputs
            if row["relationship"].get("predicate") != "requires"
        ]
        reusable = [
            row
            for row in inputs
            if row["relationship"].get("predicate") == "requires"
        ]
        outputs = sorted(
            (
                _project_recipe_slot(row)
                for row in reader.recipe_slots(
                    recipe_id,
                    PRODUCER_PREDICATES,
                )
            ),
            key=_projected_recipe_slot_order,
        )
        items.append(
            {
                "scope": current["scope"],
                "recipe": current["recipe"],
                "roles": sorted(current["roles"]),
                "mechanics": current["mechanics"],
                "occurrences": current["occurrences"],
                "ingredient_slots": {
                    "mode": "AND",
                    "slots": consumed,
                },
                "reusable_requirements": {
                    "mode": "AND",
                    "slots": reusable,
                },
                "output_slots": outputs,
            }
        )

    projection: dict[str, Any] = {
        "status": (
            "answered"
            if producers["target"]["resolved"]
            or consumers["target"]["resolved"]
            else "unresolved"
        ),
        "source_pages": {
            "producers": producers["page"],
            "consumers": consumers["page"],
        },
        "items": items,
        "non_recipe_occurrences": non_recipe_occurrences,
    }
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _non_executable_recipe_occurrences(
    reader: RuntimeGraphReader,
    *,
    selector: NodeSelector,
    scopes: tuple[Scope, ...],
) -> list[tuple[str, dict[str, Any]]]:
    """Find target-adjacent recipe rows intentionally excluded from Gate A."""

    domain = RuntimeGraphDomainQuery(reader)
    resolution = domain.resolve_targets(
        selector,
        _domain_graph_scope(scopes),
    )
    result: list[tuple[str, dict[str, Any]]] = []
    for target in resolution.targets:
        for role, predicates in (
            ("producer", PRODUCER_PREDICATES),
            ("consumer", CONSUMER_PREDICATES),
        ):
            closure = domain.target_match_closure(target, role)
            matches_by_id: dict[str, list[Any]] = defaultdict(list)
            for match in closure.matches:
                matches_by_id[str(match.node["id"])].append(match)
            alternative_ids = tuple(sorted(matches_by_id))
            if not alternative_ids:
                continue
            alternatives = ",".join("?" for _ in alternative_ids)
            predicate_placeholders = ",".join("?" for _ in predicates)
            try:
                rows = reader.connection.execute(
                    "SELECT owner.json, owner_edge.json, slot.json, "
                    "alternative_edge.json, alternative.json "
                    "FROM edges AS alternative_edge "
                    "JOIN nodes AS alternative "
                    "ON alternative.id = alternative_edge.object "
                    "JOIN nodes AS slot ON slot.id = alternative_edge.subject "
                    "JOIN edges AS owner_edge ON owner_edge.object = slot.id "
                    "JOIN nodes AS owner ON owner.id = owner_edge.subject "
                    "WHERE alternative_edge.predicate = 'accepts_alternative' "
                    f"AND alternative_edge.object IN ({alternatives}) "
                    f"AND owner_edge.predicate IN ({predicate_placeholders}) "
                    "AND alternative_edge.profile = ? "
                    "AND alternative_edge.physical_side = ? "
                    "AND owner_edge.profile = ? "
                    "AND owner_edge.physical_side = ? "
                    "AND alternative.profile = ? "
                    "AND alternative.physical_side = ? "
                    "AND slot.profile = ? "
                    "AND slot.physical_side = ? "
                    "AND owner.profile = ? "
                    "AND owner.physical_side = ? "
                    "AND owner.kind = 'recipe' "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM edges AS membership "
                    "WHERE membership.object = owner.id "
                    "AND membership.predicate = 'has_recipe' "
                    "AND membership.profile = owner.profile "
                    "AND membership.physical_side = owner.physical_side "
                    "AND json_extract("
                    "membership.json, '$.attributes.lookup_active'"
                    ") = 1) "
                    "ORDER BY owner.id, owner_edge.predicate, slot.id, "
                    "alternative.id, alternative_edge.id",
                    (
                        *alternative_ids,
                        *predicates,
                        target.scope.profile,
                        target.scope.physical_side,
                        target.scope.profile,
                        target.scope.physical_side,
                        target.scope.profile,
                        target.scope.physical_side,
                        target.scope.profile,
                        target.scope.physical_side,
                        target.scope.profile,
                        target.scope.physical_side,
                    ),
                ).fetchall()
            except sqlite3.Error as exc:
                raise CorpusBridgeError(
                    f"cannot query non-executable classification rows: {exc}"
                ) from exc
            for (
                encoded_owner,
                encoded_owner_edge,
                encoded_slot,
                encoded_alternative_edge,
                encoded_alternative,
            ) in rows:
                owner = decode_record(encoded_owner, "node")
                owner_edge = decode_record(encoded_owner_edge, "edge")
                slot = decode_record(encoded_slot, "node")
                alternative_edge = decode_record(
                    encoded_alternative_edge,
                    "edge",
                )
                alternative = decode_record(encoded_alternative, "node")
                matched = [
                    {
                        "node": match.node,
                        "path": list(match.path),
                        "match_role": match.match_role,
                    }
                    for match in matches_by_id[str(alternative["id"])]
                ]
                result.append(
                    (
                        role,
                        {
                            "scope": target.scope.to_dict(),
                            "target": target.node,
                            "owner": owner,
                            "owner_relationship": owner_edge,
                            "slot": slot,
                            "matched_alternatives": [
                                {
                                    "relationship": alternative_edge,
                                    "node": alternative,
                                }
                            ],
                            "match_closure": {
                                "truncated": closure.truncated,
                                "reasons": list(closure.reasons),
                                "matches": matched,
                            },
                            "mechanics": domain.owner_mechanics(
                                owner,
                                target.scope,
                            ),
                            "semantics": {
                                "direction": (
                                    "output"
                                    if role == "producer"
                                    else "input"
                                ),
                                "conditional": owner_edge.get("predicate")
                                in {"may_produce", "may_consume"},
                                "guaranteed": owner_edge.get("predicate")
                                in {"produces", "consumes", "requires"},
                            },
                        },
                    )
                )
    result.sort(
        key=lambda row: (
            row[0],
            str(row[1]["scope"].get("profile", "")),
            str(row[1]["scope"].get("physical_side", "")),
            str(row[1]["owner"].get("id", "")),
            str(row[1]["owner_relationship"].get("id", "")),
            str(row[1]["slot"].get("id", "")),
        )
    )
    return result


def _classification_projection(
    reader: RuntimeGraphReader,
    *,
    selector: NodeSelector,
    producers: dict[str, Any],
    consumers: dict[str, Any],
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Classify exact occurrence owners and reconciled presentation rows."""

    owners: dict[tuple[str, str, str], dict[str, Any]] = {}
    presentations: dict[tuple[str, str, str], dict[str, Any]] = {}
    presentation_refs: dict[
        tuple[str, str, str],
        set[tuple[str, str]],
    ] = defaultdict(set)
    occurrences = [
        *(
            ("producer", occurrence)
            for occurrence in producers["items"]
        ),
        *(
            ("consumer", occurrence)
            for occurrence in consumers["items"]
        ),
        *_non_executable_recipe_occurrences(
            reader,
            selector=selector,
            scopes=scopes,
        ),
    ]
    for role, occurrence in occurrences:
        owner = occurrence.get("owner")
        mechanics = occurrence.get("mechanics")
        scope = occurrence.get("scope")
        if (
            not isinstance(owner, dict)
            or not isinstance(mechanics, dict)
            or not isinstance(scope, dict)
        ):
            raise CorpusBridgeError(
                f"{role} occurrence cannot be classified"
            )
        key = (
            str(scope.get("profile", "")),
            str(scope.get("physical_side", "")),
            str(owner.get("id", "")),
        )
        current = owners.get(key)
        if current is None:
            current = {
                "row_type": "runtime-owner",
                "scope": scope,
                "row": owner,
                "roles": {role},
                "classification": mechanics.get("classification"),
                "eligible": mechanics.get("eligible"),
                "mechanics": mechanics,
                "occurrences": {
                    "producers": [],
                    "consumers": [],
                },
            }
            owners[key] = current
        elif (
            current["row"] != owner
            or current["scope"] != scope
            or current["mechanics"] != mechanics
        ):
            raise CorpusBridgeError(
                "classification occurrences disagree on owner mechanics: "
                + str(owner.get("id"))
            )
        current["roles"].add(role)
        current["occurrences"][role + "s"].append(occurrence)

        for presentation in mechanics.get("presentation", []):
            if not isinstance(presentation, dict):
                raise CorpusBridgeError(
                    "classification mechanics contains invalid presentation"
                )
            relationship = presentation.get("relationship")
            source = presentation.get("source")
            if (
                not isinstance(relationship, dict)
                or not isinstance(source, dict)
            ):
                raise CorpusBridgeError(
                    "classification presentation omits exact records"
                )
            source_scope = _record_scope(source)
            presentation_key = (
                source_scope.profile,
                source_scope.physical_side,
                str(source.get("id", "")),
            )
            presentation_current = presentations.get(presentation_key)
            if presentation_current is None:
                presentation_current = {
                    "row_type": "presentation",
                    "scope": {
                        "profile": source_scope.profile,
                        "physical_side": source_scope.physical_side,
                    },
                    "row": source,
                    "roles": ["presentation"],
                    "classification": "presentation-only",
                    "eligible": False,
                    "presentation_for": [],
                }
                presentations[presentation_key] = presentation_current
            elif presentation_current["row"] != source:
                raise CorpusBridgeError(
                    "classification presentation id resolves differently: "
                    + str(source.get("id"))
                )
            reference_key = (
                str(owner.get("id", "")),
                str(relationship.get("id", "")),
            )
            if reference_key not in presentation_refs[presentation_key]:
                presentation_refs[presentation_key].add(reference_key)
                presentation_current["presentation_for"].append(
                    {
                        "relationship": relationship,
                        "runtime_row": owner,
                    }
                )

    items: list[dict[str, Any]] = []
    for key in sorted(owners):
        row = owners[key]
        row["roles"] = sorted(row["roles"])
        items.append(row)
    for key in sorted(presentations):
        row = presentations[key]
        row["presentation_for"].sort(
            key=lambda reference: (
                str(reference["runtime_row"].get("id", "")),
                str(reference["relationship"].get("id", "")),
            )
        )
        items.append(row)
    items.sort(
        key=lambda row: (
            str(row["scope"].get("profile", "")),
            str(row["scope"].get("physical_side", "")),
            str(row["row"].get("kind", "")),
            str(row["row"].get("id", "")),
        )
    )
    counts: dict[str, int] = defaultdict(int)
    for row in items:
        classification = row["classification"]
        if not isinstance(classification, str) or not classification:
            raise CorpusBridgeError(
                "classification row has no exact classification: "
                + str(row["row"].get("id"))
            )
        counts[classification] += 1
    projection: dict[str, Any] = {
        "status": (
            "answered"
            if producers["target"]["resolved"]
            or consumers["target"]["resolved"]
            else "unresolved"
        ),
        "source_pages": {
            "producers": producers["page"],
            "consumers": consumers["page"],
        },
        "items": items,
        "counts": [
            {
                "classification": classification,
                "count": counts[classification],
            }
            for classification in sorted(counts)
        ],
    }
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _comparison_semantic_value(
    value: Any,
    parent_key: str | None = None,
) -> Any:
    """Remove observation identity while retaining exact semantic payload."""

    if isinstance(value, dict):
        is_record = value.get("record_type") in {"node", "edge"}
        omitted = {"evidence_ids"}
        if is_record:
            omitted.update(("id", "scope"))
            if value.get("record_type") == "edge":
                omitted.update(("subject", "object"))
        else:
            projected_scope = value.get("scope")
            if (
                isinstance(projected_scope, dict)
                and {
                    "profile",
                    "physical_side",
                }.issubset(projected_scope)
            ):
                omitted.add("scope")
        return {
            key: _comparison_semantic_value(child, key)
            for key, child in sorted(value.items())
            if key not in omitted
        }
    if isinstance(value, (list, tuple)):
        normalized = [
            _comparison_semantic_value(child)
            for child in value
        ]
        if parent_key in {
            "active_recipe_memberships",
            "consultation",
            "consumers",
            "execution",
            "inactive_recipe_memberships",
            "matches",
            "presentation_for",
            "producers",
        }:
            normalized.sort(key=canonical_json_payload)
        return normalized
    return value


def _comparison_semantic_sha256(value: Any) -> str:
    return hashlib.sha256(
        canonical_json_payload(_comparison_semantic_value(value))
    ).hexdigest()


def _comparison_set_sha256(fingerprints: Sequence[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_json_payload(
            sorted(
                str(row["semantic_sha256"])
                for row in fingerprints
            )
        )
    ).hexdigest()


def _comparison_record_fingerprints(
    records: Sequence[dict[str, Any]],
) -> list[dict[str, str]]:
    result = [
        {
            "row_id": str(record["id"]),
            "semantic_key": _comparison_logical_row_key(record),
            "semantic_sha256": _comparison_semantic_sha256(record),
        }
        for record in records
    ]
    if len({row["semantic_key"] for row in result}) != len(result):
        raise CorpusBridgeError(
            "comparison target semantic keys are duplicated"
        )
    result.sort(
        key=lambda row: (
            row["semantic_key"],
            row["semantic_sha256"],
            row["row_id"],
        )
    )
    return result


def _comparison_item_fingerprints(
    items: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = [
        {
            "row_id": str(item["row"]["id"]),
            "semantic_key": _comparison_logical_row_key(item["row"]),
            "classification": str(item["classification"]),
            "roles": list(item["roles"]),
            "semantic_sha256": _comparison_semantic_sha256(item),
        }
        for item in items
    ]
    result.sort(
        key=lambda row: (
            row["semantic_key"],
            row["semantic_sha256"],
            row["classification"],
            row["roles"],
            row["row_id"],
        )
    )
    if len({row["semantic_key"] for row in result}) != len(result):
        raise CorpusBridgeError(
            "comparison observation semantic keys are duplicated"
        )
    return result


def _comparison_logical_row_key(record: dict[str, Any]) -> str:
    identifier = str(record.get("id", ""))
    if identifier.startswith("rg:") and identifier.count(":") >= 2:
        return identifier.split(":", 2)[2]
    return (
        str(record.get("kind", "record"))
        + ":"
        + _comparison_semantic_sha256(record)
    )


def _comparison_pointer(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def _comparison_value_differences(
    left: Any,
    right: Any,
    path: str = "",
) -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [
            {
                "path": path or "/",
                "left": {"state": "present", "value": left},
                "right": {"state": "present", "value": right},
            }
        ]
    if isinstance(left, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child_path = path + "/" + _comparison_pointer(key)
            if key not in left:
                result.append(
                    {
                        "path": child_path,
                        "left": {"state": "missing"},
                        "right": {
                            "state": "present",
                            "value": right[key],
                        },
                    }
                )
            elif key not in right:
                result.append(
                    {
                        "path": child_path,
                        "left": {
                            "state": "present",
                            "value": left[key],
                        },
                        "right": {"state": "missing"},
                    }
                )
            else:
                result.extend(
                    _comparison_value_differences(
                        left[key],
                        right[key],
                        child_path,
                    )
                )
        return result
    if isinstance(left, list):
        result = []
        for index in range(max(len(left), len(right))):
            child_path = path + f"/{index}"
            if index >= len(left):
                result.append(
                    {
                        "path": child_path,
                        "left": {"state": "missing"},
                        "right": {
                            "state": "present",
                            "value": right[index],
                        },
                    }
                )
            elif index >= len(right):
                result.append(
                    {
                        "path": child_path,
                        "left": {
                            "state": "present",
                            "value": left[index],
                        },
                        "right": {"state": "missing"},
                    }
                )
            else:
                result.extend(
                    _comparison_value_differences(
                        left[index],
                        right[index],
                        child_path,
                    )
                )
        return result
    if left == right:
        return []
    return [
        {
            "path": path or "/",
            "left": {"state": "present", "value": left},
            "right": {"state": "present", "value": right},
        }
    ]


def _comparison_row_delta(
    left: dict[str, Any],
    right: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    if status == "not-comparable":
        return {
            "status": status,
            "equivalent_keys": [],
            "changed": [],
            "left_only": [],
            "right_only": [],
        }
    left_fingerprints = {
        row["semantic_key"]: row
        for row in left["semantic_fingerprints"]
    }
    right_fingerprints = {
        row["semantic_key"]: row
        for row in right["semantic_fingerprints"]
    }
    left_values = {
        _comparison_logical_row_key(
            row["row"] if isinstance(row.get("row"), dict) else row
        ): row
        for row in left.get("nodes", left.get("items", []))
    }
    right_values = {
        _comparison_logical_row_key(
            row["row"] if isinstance(row.get("row"), dict) else row
        ): row
        for row in right.get("nodes", right.get("items", []))
    }
    equivalent_keys: list[str] = []
    changed: list[dict[str, Any]] = []
    for key in sorted(set(left_fingerprints) & set(right_fingerprints)):
        left_fingerprint = left_fingerprints[key]
        right_fingerprint = right_fingerprints[key]
        if (
            left_fingerprint["semantic_sha256"]
            == right_fingerprint["semantic_sha256"]
        ):
            equivalent_keys.append(key)
            continue
        differences = _comparison_value_differences(
            _comparison_semantic_value(left_values[key]),
            _comparison_semantic_value(right_values[key]),
        )
        if not differences:
            raise CorpusBridgeError(
                "comparison semantic hashes differ without a field delta"
            )
        changed.append(
            {
                "semantic_key": key,
                "left_row_id": left_fingerprint["row_id"],
                "right_row_id": right_fingerprint["row_id"],
                "differences": differences,
            }
        )
    return {
        "status": status,
        "equivalent_keys": equivalent_keys,
        "changed": changed,
        "left_only": [
            left_fingerprints[key]
            for key in sorted(set(left_fingerprints) - set(right_fingerprints))
        ],
        "right_only": [
            right_fingerprints[key]
            for key in sorted(set(right_fingerprints) - set(left_fingerprints))
        ],
    }


def _mechanics_only_classification_item(
    item: dict[str, Any],
) -> dict[str, Any]:
    """Remove cross-profile presentation from one runtime-owner projection."""

    mechanics = item["mechanics"]
    cleaned_mechanics = {
        key: value
        for key, value in mechanics.items()
        if key not in {"evidence_ids", "presentation"}
    }
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for role in ("producers", "consumers"):
        occurrences[role] = []
        for occurrence in item["occurrences"][role]:
            cleaned_occurrence = {
                key: value
                for key, value in occurrence.items()
                if key not in {"evidence_ids", "mechanics"}
            }
            occurrence_mechanics = occurrence["mechanics"]
            cleaned_occurrence["mechanics"] = {
                key: value
                for key, value in occurrence_mechanics.items()
                if key not in {"evidence_ids", "presentation"}
            }
            occurrences[role].append(cleaned_occurrence)
    return {
        "row_type": "runtime-owner",
        "scope": item["scope"],
        "row": item["row"],
        "roles": item["roles"],
        "classification": item["classification"],
        "eligible": item["eligible"],
        "mechanics": cleaned_mechanics,
        "occurrences": occurrences,
    }


def _comparison_scope_key(scope: dict[str, Any]) -> str:
    return f"{scope['profile']}/{scope['physical_side']}"


def _comparison_dimension(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    comparable_statuses: set[str],
) -> str:
    if (
        left["status"] not in comparable_statuses
        or right["status"] not in comparable_statuses
    ):
        return "not-comparable"
    return (
        "equivalent"
        if left["semantic_set_sha256"] == right["semantic_set_sha256"]
        else "different"
    )


def _comparison_pairwise(
    scope_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for left_index, left in enumerate(scope_rows):
        for right in scope_rows[left_index + 1 :]:
            left_target = left["target"]
            right_target = right["target"]
            target_state = _comparison_dimension(
                left_target,
                right_target,
                comparable_statuses={"observed"},
            )
            mechanics_state = _comparison_dimension(
                left["mechanics"],
                right["mechanics"],
                comparable_statuses={"observed", "none-observed"},
            )
            presentation_state = _comparison_dimension(
                left["presentation"],
                right["presentation"],
                comparable_statuses={
                    "observed-via-reconciliation",
                    "none-observed",
                },
            )
            dimensions = {
                "target": target_state,
                "mechanics": mechanics_state,
                "presentation": presentation_state,
            }
            result.append(
                {
                    "left_scope": _comparison_scope_key(left["scope"]),
                    "right_scope": _comparison_scope_key(right["scope"]),
                    **dimensions,
                    "target_delta": _comparison_row_delta(
                        left["target"],
                        right["target"],
                        target_state,
                    ),
                    "mechanics_delta": _comparison_row_delta(
                        left["mechanics"],
                        right["mechanics"],
                        mechanics_state,
                    ),
                    "presentation_delta": _comparison_row_delta(
                        left["presentation"],
                        right["presentation"],
                        presentation_state,
                    ),
                    "difference_codes": sorted(
                        name
                        for name, status in dimensions.items()
                        if status == "different"
                    ),
                    "not_comparable": sorted(
                        name
                        for name, status in dimensions.items()
                        if status == "not-comparable"
                    ),
                    "evidence_ids": sorted(
                        set(left["evidence_ids"])
                        | set(right["evidence_ids"])
                    ),
                }
            )
    return result


def _comparison_equivalence_groups(
    scope_rows: Sequence[dict[str, Any]],
    field: str,
    comparable_statuses: set[str],
) -> list[dict[str, Any]]:
    scopes_by_digest: dict[str, list[str]] = defaultdict(list)
    for row in scope_rows:
        section = row[field]
        if section["status"] not in comparable_statuses:
            continue
        scopes_by_digest[section["semantic_set_sha256"]].append(
            _comparison_scope_key(row["scope"])
        )
    return [
        {
            "semantic_set_sha256": digest,
            "scopes": sorted(scopes_by_digest[digest]),
        }
        for digest in sorted(scopes_by_digest)
    ]


def _comparison_summary(
    scope_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "observed_target_scopes": [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
            if row["target"]["status"] == "observed"
        ],
        "missing_target_scopes": [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
            if row["target"]["status"] == "missing"
        ],
        "ambiguous_target_scopes": [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
            if row["target"]["status"] == "ambiguous"
        ],
        "mechanics_equivalence_groups": _comparison_equivalence_groups(
            scope_rows,
            "mechanics",
            {"observed", "none-observed"},
        ),
        "mechanics_unavailable_scopes": [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
            if row["mechanics"]["status"]
            not in {"observed", "none-observed"}
        ],
        "presentation_equivalence_groups": _comparison_equivalence_groups(
            scope_rows,
            "presentation",
            {"observed-via-reconciliation", "none-observed"},
        ),
        "presentation_observed_scopes": [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
            if row["presentation"]["status"]
            == "observed-via-reconciliation"
        ],
        "presentation_unavailable_scopes": [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
            if row["presentation"]["status"]
            not in {"observed-via-reconciliation", "none-observed"}
        ],
    }


def _comparison_projection(
    *,
    classification: dict[str, Any],
    runtime_nodes: Sequence[dict[str, Any]],
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare exact target-relative observations without mixing authority."""

    target_by_scope: dict[Scope, list[dict[str, Any]]] = defaultdict(list)
    for node in runtime_nodes:
        target_by_scope[_record_scope(node)].append(node)
    mechanics_by_scope: dict[Scope, list[dict[str, Any]]] = defaultdict(list)
    presentation_by_scope: dict[Scope, list[dict[str, Any]]] = defaultdict(list)
    for item in classification["items"]:
        item_scope = Scope(
            item["scope"]["profile"],
            item["scope"]["physical_side"],
        )
        if item["row_type"] == "runtime-owner":
            mechanics_by_scope[item_scope].append(
                _mechanics_only_classification_item(item)
            )
        elif item["row_type"] == "presentation":
            presentation_by_scope[item_scope].append(item)
        else:
            raise CorpusBridgeError(
                "comparison received an unsupported classified row type"
            )

    scope_rows: list[dict[str, Any]] = []
    for scope in scopes:
        target_nodes = sorted(
            target_by_scope.get(scope, []),
            key=lambda row: str(row["id"]),
        )
        target_status = (
            "missing"
            if not target_nodes
            else "observed"
            if len(target_nodes) == 1
            else "ambiguous"
        )
        target_fingerprints = _comparison_record_fingerprints(target_nodes)
        target_section: dict[str, Any] = {
            "status": target_status,
            "nodes": target_nodes,
            "semantic_fingerprints": target_fingerprints,
            "semantic_set_sha256": _comparison_set_sha256(
                target_fingerprints
            ),
        }
        target_section["evidence_ids"] = sorted(
            _validated_runtime_projection_records(
                target_section,
                scopes=scopes,
                snapshot_id=snapshot_id,
            )
        )

        mechanics_items = sorted(
            mechanics_by_scope.get(scope, []),
            key=lambda row: (
                str(row["row"].get("kind", "")),
                str(row["row"].get("id", "")),
            ),
        )
        mechanics_status = (
            "observed"
            if mechanics_items
            else "none-observed"
            if target_status == "observed"
            else "ambiguous-target"
            if target_status == "ambiguous"
            else "unavailable"
        )
        mechanics_fingerprints = _comparison_item_fingerprints(
            mechanics_items
        )
        mechanics_section: dict[str, Any] = {
            "status": mechanics_status,
            "items": mechanics_items,
            "semantic_fingerprints": mechanics_fingerprints,
            "semantic_set_sha256": _comparison_set_sha256(
                mechanics_fingerprints
            ),
        }
        mechanics_section["evidence_ids"] = sorted(
            _validated_runtime_projection_records(
                mechanics_section,
                scopes=scopes,
                snapshot_id=snapshot_id,
            )
        )

        presentation_items = sorted(
            presentation_by_scope.get(scope, []),
            key=lambda row: (
                str(row["row"].get("kind", "")),
                str(row["row"].get("id", "")),
            ),
        )
        presentation_status = (
            "observed-via-reconciliation"
            if presentation_items
            else "none-observed"
            if target_status == "observed"
            else "ambiguous-target"
            if target_status == "ambiguous"
            else "unavailable"
        )
        presentation_fingerprints = _comparison_item_fingerprints(
            presentation_items
        )
        presentation_section: dict[str, Any] = {
            "status": presentation_status,
            "items": presentation_items,
            "semantic_fingerprints": presentation_fingerprints,
            "semantic_set_sha256": _comparison_set_sha256(
                presentation_fingerprints
            ),
        }
        presentation_section["evidence_ids"] = sorted(
            _validated_runtime_projection_records(
                presentation_section,
                scopes=scopes,
                snapshot_id=snapshot_id,
            )
        )

        scope_row = {
            "scope": {
                "profile": scope.profile,
                "physical_side": scope.physical_side,
            },
            "target": target_section,
            "mechanics": mechanics_section,
            "presentation": presentation_section,
            "evidence_ids": sorted(
                set(target_section["evidence_ids"])
                | set(mechanics_section["evidence_ids"])
                | set(presentation_section["evidence_ids"])
            ),
        }
        scope_rows.append(scope_row)

    missing_targets = any(
        row["target"]["status"] == "missing"
        for row in scope_rows
    )
    observed_targets = any(
        row["target"]["status"] == "observed"
        for row in scope_rows
    )
    projection: dict[str, Any] = {
        "status": (
            "answered-with-missing-targets"
            if observed_targets and missing_targets
            else "answered"
            if observed_targets
            else "unresolved"
        ),
        "authority_policy": COMPARISON_AUTHORITY_POLICY,
        "scopes": scope_rows,
        "pairwise": _comparison_pairwise(scope_rows),
        "summary": _comparison_summary(scope_rows),
    }
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _executable_machine_projection(
    recipes: dict[str, Any],
    *,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project only execution-proven machine bindings for each recipe."""

    items = [
        {
            "scope": row["scope"],
            "recipe": row["recipe"],
            "classification": row["mechanics"]["classification"],
            "machines": row["mechanics"]["execution"],
        }
        for row in recipes["items"]
    ]
    projection: dict[str, Any] = {
        "status": recipes["status"],
        "items": items,
        "without_executable_machine": [
            row["recipe"]["id"]
            for row in items
            if not row["machines"]
        ],
    }
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _route_detail_projection(
    routes: dict[str, Any],
    *,
    field: str,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project one already-bounded route field without re-traversing."""

    if field not in {"ingredient_slots", "reusable_requirements"}:
        raise CorpusBridgeError(f"unsupported route detail field: {field}")
    items = []
    for route in routes["items"]:
        detail = route.get(field)
        if not isinstance(detail, dict):
            raise CorpusBridgeError(
                f"route omits required detail {field}: {route.get('id')}"
            )
        items.append(
            {
                "route_id": route["id"],
                "subproblem_id": route["subproblem_id"],
                "scope": route["scope"],
                "producer": route["producer"],
                "mode": detail["mode"],
                "slots": detail["slots"],
            }
        )
    projection: dict[str, Any] = {
        "status": routes["status"],
        "target": routes["target"],
        "items": items,
        "truncation": routes["truncation"],
    }
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


BYPRODUCT_SEMANTICS = {
    "source": "retained-route-co-output-slots",
    "classification": "mechanical-co-output-not-economic-waste",
    "aggregate_yield": "not-derived",
}

RECYCLING_SEMANTICS = {
    "root_definition": "exact-deduplicated-byproduct-alternatives",
    "consumer_policy": "consumes-and-may-consume-only",
    "requires_policy": "reusable-not-recycling",
    "closure_policy": "exact-final-target-route-reentry-or-forward-cycle",
    "chance_policy": "stage-local-not-multiplied",
    "yield_policy": "not-balanced-or-optimized",
}


def _byproduct_payload(routes: dict[str, Any]) -> dict[str, Any]:
    """Return the lossless co-output projection of one bounded route DAG."""

    if not isinstance(routes, dict):
        raise CorpusBridgeError("byproducts requires a routes object")
    status = routes.get("status")
    target = routes.get("target")
    route_items = routes.get("items")
    truncation = routes.get("truncation")
    if (
        status not in {"answered", "no-mechanical-route", "unresolved"}
        or not isinstance(target, dict)
        or not isinstance(route_items, list)
        or not isinstance(truncation, dict)
    ):
        raise CorpusBridgeError(
            "byproducts requires a structurally valid route projection"
        )

    items: list[dict[str, Any]] = []
    seen_route_ids: set[str] = set()
    output_slot_count = 0
    alternative_count = 0
    guaranteed_slot_count = 0
    conditional_slot_count = 0
    routes_with_byproducts = 0
    for index, route in enumerate(route_items):
        if not isinstance(route, dict):
            raise CorpusBridgeError(
                f"byproduct source route is not an object: index {index}"
            )
        route_id = route.get("id")
        subproblem_id = route.get("subproblem_id")
        scope = route.get("scope")
        producer = route.get("producer")
        production = route.get("production")
        slots = route.get("byproducts")
        if not isinstance(route_id, str) or not route_id:
            raise CorpusBridgeError(
                f"byproduct source route has no stable id: index {index}"
            )
        if route_id in seen_route_ids:
            raise CorpusBridgeError(
                f"byproduct source routes duplicate route id: {route_id}"
            )
        seen_route_ids.add(route_id)
        if (
            not isinstance(subproblem_id, str)
            or not subproblem_id
            or not isinstance(scope, dict)
            or not isinstance(producer, dict)
            or producer.get("record_type") != "node"
            or not isinstance(production, dict)
            or not isinstance(slots, list)
        ):
            raise CorpusBridgeError(
                f"route omits required byproduct projection fields: {route_id}"
            )
        producer_id = producer.get("id")
        if not isinstance(producer_id, str) or not producer_id:
            raise CorpusBridgeError(
                f"route producer has no stable id: {route_id}"
            )

        production_slot = production.get("slot")
        production_slot_id = (
            production_slot.get("id")
            if isinstance(production_slot, dict)
            else None
        )
        if not isinstance(production_slot_id, str) or not production_slot_id:
            raise CorpusBridgeError(
                f"route selected production has no slot id: {route_id}"
            )
        if production_slot.get("record_type") != "node":
            raise CorpusBridgeError(
                f"route selected production slot is not a runtime node: {route_id}"
            )
        seen_slot_ids: set[str] = set()
        for slot_index, slot in enumerate(slots):
            context = f"{route_id}:byproducts[{slot_index}]"
            if not isinstance(slot, dict):
                raise CorpusBridgeError(
                    f"byproduct slot is not an object: {context}"
                )
            relationship = slot.get("relationship")
            slot_record = slot.get("slot")
            semantics = slot.get("semantics")
            alternatives = slot.get("alternatives")
            if (
                not isinstance(relationship, dict)
                or relationship.get("record_type") != "edge"
                or not isinstance(slot_record, dict)
                or slot_record.get("record_type") != "node"
                or not isinstance(semantics, dict)
                or not isinstance(alternatives, dict)
            ):
                raise CorpusBridgeError(
                    f"byproduct slot omits exact route records: {context}"
                )
            slot_id = slot_record.get("id")
            if not isinstance(slot_id, str) or not slot_id:
                raise CorpusBridgeError(
                    f"byproduct slot has no stable id: {context}"
                )
            if slot_id == production_slot_id:
                raise CorpusBridgeError(
                    f"selected production slot is repeated as a byproduct: {context}"
                )
            if slot_id in seen_slot_ids:
                raise CorpusBridgeError(
                    f"byproduct slot is duplicated within a route: {context}"
                )
            seen_slot_ids.add(slot_id)

            predicate = relationship.get("predicate")
            if predicate not in {"produces", "may_produce"}:
                raise CorpusBridgeError(
                    f"byproduct slot has a non-output predicate: {context}"
                )
            if (
                relationship.get("subject") != producer_id
                or relationship.get("object") != slot_id
            ):
                raise CorpusBridgeError(
                    f"byproduct owner relationship endpoints differ: {context}"
                )
            expected_conditional = predicate == "may_produce"
            if (
                semantics.get("conditional") is not expected_conditional
                or semantics.get("guaranteed") is not (
                    not expected_conditional
                )
            ):
                raise CorpusBridgeError(
                    f"byproduct slot semantics differ from its predicate: {context}"
                )

            alternative_items = alternatives.get("items")
            mode = alternatives.get("mode")
            exhaustive = alternatives.get("exhaustive")
            total = alternatives.get("total")
            returned = alternatives.get("returned")
            truncated = alternatives.get("truncated")
            if (
                not isinstance(alternative_items, list)
                or not isinstance(mode, str)
                or not mode
                or type(exhaustive) is not bool
                or type(total) is not int
                or type(returned) is not int
                or type(truncated) is not bool
                or total < 0
                or returned < 0
                or returned != len(alternative_items)
                or total < returned
                or truncated is not (returned != total)
            ):
                raise CorpusBridgeError(
                    f"byproduct alternative page is inconsistent: {context}"
                )
            if any(
                not isinstance(alternative, dict)
                or not isinstance(alternative.get("relationship"), dict)
                or alternative["relationship"].get("record_type") != "edge"
                or alternative["relationship"].get("predicate")
                != "accepts_alternative"
                or not isinstance(alternative.get("node"), dict)
                or alternative["node"].get("record_type") != "node"
                or alternative["relationship"].get("subject") != slot_id
                or alternative["relationship"].get("object")
                != alternative["node"].get("id")
                for alternative in alternative_items
            ):
                raise CorpusBridgeError(
                    f"byproduct alternative is not an object: {context}"
                )

            output_slot_count += 1
            alternative_count += returned
            if expected_conditional:
                conditional_slot_count += 1
            else:
                guaranteed_slot_count += 1

        if slots:
            routes_with_byproducts += 1
        items.append(
            {
                "route_id": route_id,
                "subproblem_id": subproblem_id,
                "scope": scope,
                "producer": producer,
                "selected_production": production,
                "slots": slots,
            }
        )

    return {
        "status": status,
        "target": target,
        "semantics": dict(BYPRODUCT_SEMANTICS),
        "items": items,
        "summary": {
            "route_count": len(items),
            "routes_with_byproducts": routes_with_byproducts,
            "output_slot_count": output_slot_count,
            "alternative_count": alternative_count,
            "guaranteed_slot_count": guaranteed_slot_count,
            "conditional_slot_count": conditional_slot_count,
        },
        "truncation": truncation,
    }


def _byproduct_projection(
    routes: dict[str, Any],
    *,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build an evidence-closed byproduct section from retained routes."""

    projection = _byproduct_payload(routes)
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _domain_scope_from_document(
    scope: dict[str, Any],
    *,
    context: str,
) -> DomainProfileScope:
    if not isinstance(scope, dict):
        raise CorpusBridgeError(f"{context} has no exact scope")
    profile = scope.get("profile")
    physical_side = scope.get("physical_side")
    if (
        not isinstance(profile, str)
        or not isinstance(physical_side, str)
        or physical_side not in PROFILE_SIDES.get(profile, set())
    ):
        raise CorpusBridgeError(f"{context} has an invalid exact scope")
    return DomainProfileScope(profile, physical_side)


def _recycling_root_seeds(
    byproducts: dict[str, Any],
) -> tuple[RecyclingRootSeed, ...]:
    """Project every retained co-output alternative into one exact seed."""

    items = byproducts.get("items")
    if not isinstance(items, list):
        raise CorpusBridgeError(
            "recycling roots require a byproducts item array"
        )
    seeds: list[RecyclingRootSeed] = []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CorpusBridgeError(
                f"byproducts item is not an object: index {item_index}"
            )
        route_id = item.get("route_id")
        subproblem_id = item.get("subproblem_id")
        producer = item.get("producer")
        scope = _domain_scope_from_document(
            item.get("scope"),
            context=f"byproducts.items[{item_index}]",
        )
        slots = item.get("slots")
        if (
            not isinstance(route_id, str)
            or not route_id
            or not isinstance(subproblem_id, str)
            or not subproblem_id
            or not isinstance(producer, dict)
            or not isinstance(slots, list)
        ):
            raise CorpusBridgeError(
                "byproducts item omits exact recycling-root origin fields"
            )
        for slot_index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise CorpusBridgeError(
                    "byproduct slot is not an object while deriving "
                    f"recycling roots: {route_id}:{slot_index}"
                )
            relationship = slot.get("relationship")
            slot_record = slot.get("slot")
            semantics = slot.get("semantics")
            alternatives = slot.get("alternatives")
            alternative_items = (
                alternatives.get("items")
                if isinstance(alternatives, dict)
                else None
            )
            if (
                not isinstance(relationship, dict)
                or not isinstance(slot_record, dict)
                or not isinstance(semantics, dict)
                or not isinstance(alternative_items, list)
            ):
                raise CorpusBridgeError(
                    "byproduct slot omits exact recycling-root records: "
                    f"{route_id}:{slot_index}"
                )
            for alternative_index, alternative in enumerate(
                alternative_items
            ):
                if (
                    not isinstance(alternative, dict)
                    or not isinstance(
                        alternative.get("relationship"),
                        dict,
                    )
                    or not isinstance(alternative.get("node"), dict)
                ):
                    raise CorpusBridgeError(
                        "byproduct alternative omits exact recycling-root "
                        "records: "
                        f"{route_id}:{slot_index}:{alternative_index}"
                    )
                seeds.append(
                    RecyclingRootSeed(
                        ScopedTarget(scope, alternative["node"]),
                        {
                            "route_id": route_id,
                            "subproblem_id": subproblem_id,
                            "producer": producer,
                            "byproduct_relationship": relationship,
                            "slot": slot_record,
                            "alternative_relationship": alternative[
                                "relationship"
                            ],
                            "local_semantics": {
                                "output": semantics,
                                "amount_source": (
                                    "exact-slot-and-relationship-records"
                                ),
                                "chance": slot.get("chance", {}),
                            },
                        },
                    )
                )
    return tuple(seeds)


def _recycling_final_targets(
    routes: dict[str, Any],
) -> tuple[ScopedTarget, ...]:
    target = routes.get("target")
    resolved = target.get("resolved") if isinstance(target, dict) else None
    if not isinstance(resolved, list):
        raise CorpusBridgeError(
            "recycling final closure requires resolved route targets"
        )
    result: list[ScopedTarget] = []
    for index, row in enumerate(resolved):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("node"), dict)
        ):
            raise CorpusBridgeError(
                f"route target resolution is invalid: index {index}"
            )
        scope = _domain_scope_from_document(
            row.get("scope"),
            context=f"routes.target.resolved[{index}]",
        )
        result.append(ScopedTarget(scope, row["node"]))
    return tuple(result)


def _recycling_route_targets(
    routes: dict[str, Any],
) -> tuple[RecyclingRouteTarget, ...]:
    subproblems = routes.get("subproblems")
    if not isinstance(subproblems, list):
        raise CorpusBridgeError(
            "recycling route re-entry requires route subproblems"
        )
    result: list[RecyclingRouteTarget] = []
    for index, row in enumerate(subproblems):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or not isinstance(row.get("target"), dict)
        ):
            raise CorpusBridgeError(
                f"route subproblem is invalid: index {index}"
            )
        scope = _domain_scope_from_document(
            row.get("scope"),
            context=f"routes.subproblems[{index}]",
        )
        result.append(
            RecyclingRouteTarget(
                row["id"],
                ScopedTarget(scope, row["target"]),
            )
        )
    return tuple(result)


def _recycling_status(
    byproducts: dict[str, Any],
    root_count: int,
) -> str:
    status = byproducts.get("status")
    if status == "no-mechanical-route":
        return "no-mechanical-route"
    if status == "unresolved":
        return "unresolved"
    if status != "answered":
        raise CorpusBridgeError(
            "recycling status requires a valid byproduct status"
        )
    return "answered" if root_count else "no-byproduct-roots"


def _recycling_payload(
    reader: RuntimeGraphReader,
    *,
    routes: dict[str, Any],
    byproducts: dict[str, Any],
    options: RecyclingOptions,
) -> dict[str, Any]:
    seeds = _recycling_root_seeds(byproducts)
    structural = build_recycling_paths(
        reader,
        seeds,
        final_targets=_recycling_final_targets(routes),
        route_targets=_recycling_route_targets(routes),
        options=options,
    )
    return {
        "status": _recycling_status(
            byproducts,
            len(structural["roots"]),
        ),
        "semantics": dict(RECYCLING_SEMANTICS),
        **structural,
    }


def _recycling_projection(
    reader: RuntimeGraphReader,
    *,
    routes: dict[str, Any],
    byproducts: dict[str, Any],
    options: RecyclingOptions,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    projection = _recycling_payload(
        reader,
        routes=routes,
        byproducts=byproducts,
        options=options,
    )
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


NO_MACHINE_ACQUISITION_BOUNDARIES = frozenset(
    {"worldgen-acquisition-no-machine-required"}
)

PREREQUISITE_ASSUMPTIONS = {
    "player_inventory": "unknown",
    "existing_infrastructure": "unknown",
    "quest_ordering": "guidance-only-not-mechanical",
    "route_preference": "none",
    "completeness": "bounded-observed-route-dag",
}

MACHINE_FORM_ROLE_POLICY = {
    "registered_mte_stack_form": frozenset({"item_variant"}),
}
MACHINE_CONSTRUCTION_ASSUMPTIONS = {
    "player_inventory": "unknown",
    "existing_infrastructure": "unknown",
    "route_preference": "none",
    "completeness": "bounded-observed-construction-forest",
    "execution_machine_closure": "enabled",
}
INFRASTRUCTURE_CANDIDATE_ASSUMPTIONS = {
    "admission_policy": "none",
    "source_interpretation": "none",
    "voltage_tier_derivation": "none",
    "completeness": "bounded-retained-operation-domains",
}
DEFAULT_CONSTRUCTION_CHAIN_OPTIONS = ChainOptions(max_routes=250)
DEFAULT_MAX_CONSTRUCTION_MACHINE_ROOTS = 250


def _route_machine_requirement(route: dict[str, Any]) -> dict[str, Any]:
    """Group exact execution bindings by machine without admitting consultants."""

    mechanics = route.get("mechanics")
    if not isinstance(mechanics, dict):
        raise CorpusBridgeError(
            f"route omits mechanics: {route.get('id')}"
        )
    execution = mechanics.get("execution")
    if not isinstance(execution, list):
        raise CorpusBridgeError(
            f"route mechanics omit execution bindings: {route.get('id')}"
        )

    by_machine: dict[str, dict[str, Any]] = {}
    for index, binding in enumerate(execution):
        if not isinstance(binding, dict):
            raise CorpusBridgeError(
                "route execution binding is not an object: "
                f"{route.get('id')}[{index}]"
            )
        machine = binding.get("machine")
        if (
            not isinstance(machine, dict)
            or machine.get("record_type") != "node"
            or machine.get("kind") != "machine"
            or not isinstance(machine.get("id"), str)
        ):
            raise CorpusBridgeError(
                "route execution binding omits its exact machine: "
                f"{route.get('id')}[{index}]"
            )
        machine_id = str(machine["id"])
        choice = by_machine.get(machine_id)
        if choice is None:
            by_machine[machine_id] = {
                "machine": machine,
                "execution_bindings": [binding],
            }
        else:
            if choice["machine"] != machine:
                raise CorpusBridgeError(
                    "route execution bindings disagree on machine record: "
                    + machine_id
                )
            choice["execution_bindings"].append(binding)

    boundaries = route.get("boundaries")
    if not isinstance(boundaries, list):
        raise CorpusBridgeError(
            f"route omits boundaries: {route.get('id')}"
        )
    boundary_statuses = {
        str(boundary.get("status"))
        for boundary in boundaries
        if isinstance(boundary, dict) and isinstance(boundary.get("status"), str)
    }
    if by_machine:
        status = "resolved"
    elif boundary_statuses & NO_MACHINE_ACQUISITION_BOUNDARIES:
        status = "not-required"
    else:
        status = "unresolved"
    return {
        "kind": "executable-machine",
        "mode": "OR",
        "status": status,
        "items": [by_machine[identifier] for identifier in sorted(by_machine)],
    }


def _route_slot_requirement(
    slot: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    alternatives = slot.get("alternatives")
    if not isinstance(alternatives, dict):
        raise CorpusBridgeError(
            f"{kind} slot omits alternatives"
        )
    mode = alternatives.get("mode")
    if mode not in {"OR", "MATCH_DOMAIN", "SYMBOLIC"}:
        raise CorpusBridgeError(
            f"{kind} slot has unsupported alternative mode: {mode}"
        )
    return {
        "kind": kind,
        "mode": mode,
        "requirement": slot,
    }


def _prerequisite_payload(routes: dict[str, Any]) -> dict[str, Any]:
    """Derive route readiness from one already-bounded route DAG."""

    operations: list[dict[str, Any]] = []
    dependency_edges: list[dict[str, str]] = []
    route_items = routes.get("items")
    if not isinstance(route_items, list):
        raise CorpusBridgeError("routes omit their item list")

    for route in route_items:
        if not isinstance(route, dict):
            raise CorpusBridgeError("route item is not an object")
        route_id = route.get("id")
        subproblem_id = route.get("subproblem_id")
        if not isinstance(route_id, str) or not isinstance(subproblem_id, str):
            raise CorpusBridgeError("route item omits its route/subproblem id")
        ingredient_slots = route.get("ingredient_slots")
        reusable_requirements = route.get("reusable_requirements")
        if (
            not isinstance(ingredient_slots, dict)
            or ingredient_slots.get("mode") != "AND"
            or not isinstance(ingredient_slots.get("slots"), list)
        ):
            raise CorpusBridgeError(
                f"route ingredient slots are not an AND group: {route_id}"
            )
        if (
            not isinstance(reusable_requirements, dict)
            or reusable_requirements.get("mode") != "AND"
            or not isinstance(reusable_requirements.get("slots"), list)
        ):
            raise CorpusBridgeError(
                f"route reusable requirements are not an AND group: {route_id}"
            )

        groups = [_route_machine_requirement(route)]
        typed_slots = (
            (
                "consumed-input",
                ingredient_slots["slots"],
            ),
            (
                "reusable-requirement",
                reusable_requirements["slots"],
            ),
        )
        for kind, slots in typed_slots:
            for slot in slots:
                if not isinstance(slot, dict):
                    raise CorpusBridgeError(
                        f"{kind} route slot is not an object: {route_id}"
                    )
                group = _route_slot_requirement(slot, kind=kind)
                groups.append(group)
                slot_record = slot.get("slot")
                alternatives = slot.get("alternatives")
                if (
                    not isinstance(slot_record, dict)
                    or not isinstance(slot_record.get("id"), str)
                    or not isinstance(alternatives, dict)
                    or not isinstance(alternatives.get("items"), list)
                ):
                    raise CorpusBridgeError(
                        f"{kind} route slot is incomplete: {route_id}"
                    )
                for alternative in alternatives["items"]:
                    if not isinstance(alternative, dict):
                        raise CorpusBridgeError(
                            f"{kind} alternative is not an object: {route_id}"
                        )
                    child_id = alternative.get("subproblem_id")
                    if child_id is None:
                        continue
                    relationship = alternative.get("relationship")
                    if (
                        not isinstance(child_id, str)
                        or not isinstance(relationship, dict)
                        or not isinstance(relationship.get("id"), str)
                    ):
                        raise CorpusBridgeError(
                            f"{kind} linked alternative is incomplete: {route_id}"
                        )
                    dependency_edges.append(
                        {
                            "from_subproblem_id": child_id,
                            "to_route_id": route_id,
                            "to_subproblem_id": subproblem_id,
                            "requirement_kind": kind,
                            "slot_id": str(slot_record["id"]),
                            "alternative_relationship_id": str(
                                relationship["id"]
                            ),
                        }
                    )

        scope = route.get("scope")
        producer = route.get("producer")
        production = route.get("production")
        classification = route.get("classification")
        boundaries = route.get("boundaries")
        if (
            not isinstance(scope, dict)
            or not isinstance(producer, dict)
            or not isinstance(production, dict)
            or not isinstance(classification, str)
            or not isinstance(boundaries, list)
        ):
            raise CorpusBridgeError(
                f"route omits readiness context: {route_id}"
            )
        operations.append(
            {
                "route_id": route_id,
                "subproblem_id": subproblem_id,
                "scope": scope,
                "producer": producer,
                "production": production,
                "classification": classification,
                "requirements": {
                    "mode": "AND",
                    "groups": groups,
                },
                "boundaries": boundaries,
            }
        )

    dependency_edges.sort(
        key=lambda row: (
            row["to_subproblem_id"],
            row["to_route_id"],
            row["requirement_kind"],
            row["slot_id"],
            row["alternative_relationship_id"],
            row["from_subproblem_id"],
        )
    )
    return {
        "status": routes["status"],
        "target": routes["target"],
        "roots": routes["roots"],
        "subproblems": routes["subproblems"],
        "assumptions": dict(PREREQUISITE_ASSUMPTIONS),
        "operations": operations,
        "dependency_edges": dependency_edges,
        "cycles": routes["cycles"],
        "unresolved": routes["unresolved_leaves"],
        "truncation": routes["truncation"],
    }


def _prerequisite_projection(
    routes: dict[str, Any],
    *,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    projection = _prerequisite_payload(routes)
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _machine_construction_seeds(
    prerequisites: dict[str, Any],
) -> list[dict[str, Any]]:
    """Aggregate exact construction roots without losing route provenance.

    This is the stable handoff between route readiness and the separately
    bounded machine-construction traversal.  It deliberately stops at exact
    execution-proven machine nodes; form resolution and producer traversal
    belong to the construction projection.
    """

    operations = prerequisites.get("operations")
    if not isinstance(operations, list):
        raise CorpusBridgeError(
            "machine construction requires prerequisite operations"
        )

    by_machine: dict[str, dict[str, Any]] = {}
    for operation_index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise CorpusBridgeError(
                "prerequisite operation is not an object: "
                f"{operation_index}"
            )
        route_id = operation.get("route_id")
        subproblem_id = operation.get("subproblem_id")
        requirements = operation.get("requirements")
        if (
            not isinstance(route_id, str)
            or not isinstance(subproblem_id, str)
            or not isinstance(requirements, dict)
            or requirements.get("mode") != "AND"
            or not isinstance(requirements.get("groups"), list)
        ):
            raise CorpusBridgeError(
                "prerequisite operation is incomplete for machine "
                f"construction: {operation_index}"
            )
        machine_groups = [
            group
            for group in requirements["groups"]
            if isinstance(group, dict)
            and group.get("kind") == "executable-machine"
        ]
        if len(machine_groups) != 1:
            raise CorpusBridgeError(
                "prerequisite operation must contain exactly one executable-"
                f"machine group: {route_id}"
            )
        machine_group = machine_groups[0]
        items = machine_group.get("items")
        if not isinstance(items, list):
            raise CorpusBridgeError(
                f"prerequisite machine group omits items: {route_id}"
            )

        seen_operation_machines: set[str] = set()
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                raise CorpusBridgeError(
                    "prerequisite machine item is not an object: "
                    f"{route_id}[{item_index}]"
                )
            machine = item.get("machine")
            execution_bindings = item.get("execution_bindings")
            if (
                not isinstance(machine, dict)
                or machine.get("record_type") != "node"
                or machine.get("kind") != "machine"
                or not isinstance(machine.get("id"), str)
                or not isinstance(execution_bindings, list)
                or not execution_bindings
                or any(
                    not isinstance(binding, dict)
                    for binding in execution_bindings
                )
            ):
                raise CorpusBridgeError(
                    "prerequisite machine item is incomplete: "
                    f"{route_id}[{item_index}]"
                )
            machine_id = str(machine["id"])
            if machine_id in seen_operation_machines:
                raise CorpusBridgeError(
                    "prerequisite operation repeats an exact machine: "
                    f"{route_id}: {machine_id}"
                )
            seen_operation_machines.add(machine_id)
            seed = by_machine.get(machine_id)
            if seed is None:
                seed = {
                    "machine": machine,
                    "required_by": [],
                }
                by_machine[machine_id] = seed
            elif seed["machine"] != machine:
                raise CorpusBridgeError(
                    "prerequisite operations disagree on machine record: "
                    + machine_id
                )
            seed["required_by"].append(
                {
                    "source": "material-route",
                    "route_id": route_id,
                    "subproblem_id": subproblem_id,
                    "execution_bindings": execution_bindings,
                }
            )

    result = [by_machine[identifier] for identifier in sorted(by_machine)]
    for seed in result:
        seed["required_by"].sort(
            key=lambda row: (
                row["route_id"],
                row["subproblem_id"],
            )
        )
    return result


def _machine_form_path_admission(path: dict[str, Any]) -> str:
    """Classify one exact machine-to-form path under the v1 policy."""

    machine = path.get("machine")
    relationship = path.get("relationship")
    form = path.get("form")
    if (
        not isinstance(machine, dict)
        or not isinstance(relationship, dict)
        or not isinstance(form, dict)
        or relationship.get("record_type") != "edge"
        or relationship.get("predicate") != "has_form"
        or relationship.get("subject") != machine.get("id")
        or relationship.get("object") != form.get("id")
        or _record_scope(machine) != _record_scope(relationship)
        or _record_scope(machine) != _record_scope(form)
    ):
        raise CorpusBridgeError(
            "machine form path has invalid endpoints or scope"
        )
    attributes = relationship.get("attributes")
    role = (
        attributes.get("role")
        if isinstance(attributes, dict)
        else None
    )
    admitted_kinds = MACHINE_FORM_ROLE_POLICY.get(str(role))
    if admitted_kinds is None:
        return "unsupported-role"
    if form.get("kind") not in admitted_kinds:
        return "unsupported-form-kind"
    return "admitted"


def _machine_form_resolution(
    domain: RuntimeGraphDomainQuery,
    machine: dict[str, Any],
) -> dict[str, Any]:
    scope = _record_scope(machine)
    paths = [
        {
            **path,
            "admission": _machine_form_path_admission(path),
        }
        for path in domain.machine_form_paths(
            ScopedTarget(
                DomainProfileScope(scope.profile, scope.physical_side),
                machine,
            )
        )
    ]
    admitted = [
        path for path in paths if path["admission"] == "admitted"
    ]
    status = (
        "resolved"
        if len(admitted) == 1
        else "ambiguous"
        if len(admitted) > 1
        else "unresolved"
    )
    return {
        "status": status,
        "paths": paths,
        "selected_relationship_id": (
            admitted[0]["relationship"]["id"]
            if len(admitted) == 1
            else None
        ),
        "root_subproblem_id": None,
    }


def _selected_machine_form(
    resolution: dict[str, Any],
) -> dict[str, Any] | None:
    selected = resolution.get("selected_relationship_id")
    if not isinstance(selected, str):
        return None
    matches = [
        path
        for path in resolution.get("paths", [])
        if isinstance(path, dict)
        and isinstance(path.get("relationship"), dict)
        and path["relationship"].get("id") == selected
        and path.get("admission") == "admitted"
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("form"), dict):
        raise CorpusBridgeError(
            "resolved machine form does not select exactly one admitted path"
        )
    return matches[0]["form"]


def _construction_dependency_rows(
    forest: dict[str, Any],
    machine_dependencies: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for route in forest["items"]:
        for field, kind in (
            ("ingredient_slots", "consumed-input"),
            ("reusable_requirements", "reusable-requirement"),
        ):
            detail = route.get(field)
            slots = detail.get("slots") if isinstance(detail, dict) else None
            if not isinstance(slots, list):
                raise CorpusBridgeError(
                    f"construction route omits {field}: {route.get('id')}"
                )
            for slot in slots:
                alternatives = slot.get("alternatives")
                items = (
                    alternatives.get("items")
                    if isinstance(alternatives, dict)
                    else None
                )
                slot_record = slot.get("slot")
                if (
                    not isinstance(items, list)
                    or not isinstance(slot_record, dict)
                    or not isinstance(slot_record.get("id"), str)
                ):
                    raise CorpusBridgeError(
                        "construction route slot is incomplete: "
                        f"{route.get('id')}"
                    )
                for alternative in items:
                    child = (
                        alternative.get("subproblem_id")
                        if isinstance(alternative, dict)
                        else None
                    )
                    if child is None:
                        continue
                    relationship = alternative.get("relationship")
                    if (
                        not isinstance(child, str)
                        or not isinstance(relationship, dict)
                        or not isinstance(relationship.get("id"), str)
                    ):
                        raise CorpusBridgeError(
                            "construction alternative dependency is incomplete"
                        )
                    dependencies.append(
                        {
                            "kind": kind,
                            "from_subproblem_id": route["subproblem_id"],
                            "to_subproblem_id": child,
                            "route_id": route["id"],
                            "slot_id": slot_record["id"],
                            "relationship_id": relationship["id"],
                        }
                    )
    for dependency in machine_dependencies:
        target = dependency.get("to_subproblem_id")
        if not isinstance(target, str):
            continue
        dependencies.append(
            {
                "kind": "execution-machine",
                "from_subproblem_id": dependency["from_subproblem_id"],
                "to_subproblem_id": target,
                "route_id": dependency["route_id"],
                "machine_id": dependency["machine"]["id"],
            }
        )
    dependencies.sort(
        key=lambda row: (
            row["from_subproblem_id"],
            row["to_subproblem_id"],
            row["route_id"],
            row["kind"],
            str(row.get("slot_id", "")),
            str(row.get("relationship_id", "")),
            str(row.get("machine_id", "")),
        )
    )
    return dependencies


def _construction_cycles(
    forest: dict[str, Any],
    machine_dependencies: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    dependencies = _construction_dependency_rows(
        forest,
        machine_dependencies,
    )
    vertices = {
        row["id"] for row in forest["subproblems"]
    }
    adjacency = {identifier: [] for identifier in vertices}
    for dependency in dependencies:
        source = dependency["from_subproblem_id"]
        target = dependency["to_subproblem_id"]
        if source in adjacency and target in adjacency:
            adjacency[source].append(target)
    for identifier in adjacency:
        adjacency[identifier] = sorted(set(adjacency[identifier]))

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
        if lowlinks[vertex] != indices[vertex]:
            return
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

    cycles: list[dict[str, Any]] = []
    for members in sorted(components):
        member_set = set(members)
        cycle_dependencies = [
            row
            for row in dependencies
            if row["from_subproblem_id"] in member_set
            and row["to_subproblem_id"] in member_set
        ]
        identity = "\x1f".join(members)
        cycles.append(
            {
                "id": (
                    "construction-cycle:"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                ),
                "subproblem_ids": list(members),
                "dependencies": cycle_dependencies,
            }
        )
    return cycles


def _empty_construction_forest(options: ChainOptions) -> dict[str, Any]:
    return {
        "status": "no-mechanical-route",
        "target": {
            "selectors": [],
            "resolved": [],
            "gaps": [],
        },
        "roots": [],
        "subproblems": [],
        "items": [],
        "cycles": [],
        "unresolved_leaves": [],
        "truncation": {
            "truncated": False,
            "reasons": [],
            "limits": options.limits(),
        },
    }


def _machine_construction_payload(
    reader: RuntimeGraphReader,
    prerequisites: dict[str, Any],
    *,
    options: ChainOptions,
    max_machine_roots: int,
) -> dict[str, Any]:
    if type(max_machine_roots) is not int or max_machine_roots < 1:
        raise CorpusBridgeError(
            "max construction machine roots must be a positive integer"
        )
    domain = RuntimeGraphDomainQuery(reader)
    initial_seeds = _machine_construction_seeds(prerequisites)
    machine_state: dict[str, dict[str, Any]] = {}
    form_targets: dict[str, ScopedTarget] = {}
    root_limit_machine_ids: set[str] = set()
    machine_dependency_state: dict[
        tuple[str, str, str], dict[str, Any]
    ] = {}

    def add_requirement(
        machine: dict[str, Any],
        requirement: dict[str, Any],
    ) -> ScopedTarget | None:
        if (
            not isinstance(machine, dict)
            or machine.get("record_type") != "node"
            or machine.get("kind") != "machine"
            or not isinstance(machine.get("id"), str)
        ):
            raise CorpusBridgeError(
                "machine construction requirement lacks an exact machine"
            )
        machine_id = str(machine["id"])
        state = machine_state.get(machine_id)
        if state is None:
            state = {
                "machine": machine,
                "required_by": [],
                "form_resolution": _machine_form_resolution(
                    domain,
                    machine,
                ),
            }
            machine_state[machine_id] = state
        elif state["machine"] != machine:
            raise CorpusBridgeError(
                "machine construction requirements disagree on machine: "
                + machine_id
            )
        if requirement not in state["required_by"]:
            state["required_by"].append(requirement)

        form = _selected_machine_form(state["form_resolution"])
        if form is None:
            return None
        form_id = str(form["id"])
        existing = form_targets.get(form_id)
        if existing is not None:
            return None
        if len(form_targets) >= max_machine_roots:
            root_limit_machine_ids.add(machine_id)
            return None
        scope = _record_scope(form)
        target = ScopedTarget(
            DomainProfileScope(scope.profile, scope.physical_side),
            form,
        )
        form_targets[form_id] = target
        return target

    for seed in initial_seeds:
        for requirement in seed["required_by"]:
            add_requirement(seed["machine"], requirement)
    if len(form_targets) > max_machine_roots:
        raise CorpusBridgeError(
            "initial construction forms exceed max machine roots"
        )

    def additional_roots(
        context: dict[str, Any],
    ) -> tuple[ScopedTarget, ...]:
        mechanics = context.get("mechanics")
        execution = (
            mechanics.get("execution")
            if isinstance(mechanics, dict)
            else None
        )
        if not isinstance(execution, (list, tuple)):
            raise CorpusBridgeError(
                "construction route mechanics omit execution bindings"
            )
        by_machine: dict[str, dict[str, Any]] = {}
        for binding in execution:
            machine = binding.get("machine") if isinstance(binding, dict) else None
            if (
                not isinstance(machine, dict)
                or not isinstance(machine.get("id"), str)
            ):
                raise CorpusBridgeError(
                    "construction execution binding omits its exact machine"
                )
            machine_id = str(machine["id"])
            grouped = by_machine.setdefault(
                machine_id,
                {"machine": machine, "bindings": []},
            )
            if grouped["machine"] != machine:
                raise CorpusBridgeError(
                    "construction route execution bindings disagree on machine"
                )
            grouped["bindings"].append(binding)

        additions: dict[str, ScopedTarget] = {}
        for machine_id in sorted(by_machine):
            grouped = by_machine[machine_id]
            requirement = {
                "source": "construction-route",
                "route_id": context["route_id"],
                "subproblem_id": context["subproblem_id"],
                "execution_bindings": grouped["bindings"],
            }
            target = add_requirement(grouped["machine"], requirement)
            dependency_key = (
                context["route_id"],
                context["subproblem_id"],
                machine_id,
            )
            machine_dependency_state[dependency_key] = {
                "route_id": context["route_id"],
                "from_subproblem_id": context["subproblem_id"],
                "machine": grouped["machine"],
                "execution_bindings": grouped["bindings"],
                "to_subproblem_id": None,
                "status": "pending",
            }
            if target is not None:
                additions[str(target.node["id"])] = target
        return tuple(additions[identifier] for identifier in sorted(additions))

    if form_targets:
        result = build_process_chain_from_targets(
            reader,
            tuple(form_targets[identifier] for identifier in sorted(form_targets)),
            options=options,
            additional_root_provider=additional_roots,
        )
        forest = {
            "status": (
                "answered"
                if result["routes"]
                else "no-mechanical-route"
            ),
            "target": result["target"],
            "roots": result["roots"],
            "subproblems": result["subproblems"],
            "items": result["routes"],
            "cycles": result["cycles"],
            "unresolved_leaves": result["unresolved_leaves"],
            "truncation": result["truncation"],
        }
    else:
        forest = _empty_construction_forest(options)

    root_ids = set(forest["roots"])
    root_by_form_id = {
        row["target"]["id"]: row["id"]
        for row in forest["subproblems"]
        if row["id"] in root_ids
    }
    machines: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for machine_id in sorted(machine_state):
        state = machine_state[machine_id]
        state["required_by"].sort(
            key=lambda row: (
                row["source"],
                row["route_id"],
                row["subproblem_id"],
            )
        )
        resolution = state["form_resolution"]
        form = _selected_machine_form(resolution)
        if form is not None:
            resolution["root_subproblem_id"] = root_by_form_id.get(
                form["id"]
            )
        origins = sorted(
            {row["source"] for row in state["required_by"]}
        )
        machine_row = {
            "machine": state["machine"],
            "origins": origins,
            "required_by": state["required_by"],
            "form_resolution": resolution,
        }
        machines.append(machine_row)
        reason: str | None = None
        if resolution["status"] == "unresolved":
            reason = "unresolved-form"
        elif resolution["status"] == "ambiguous":
            reason = "ambiguous-form"
        elif machine_id in root_limit_machine_ids:
            reason = "max-machine-roots"
        elif resolution["root_subproblem_id"] is None:
            reason = "unseeded-form-root"
        if reason is not None:
            unresolved.append(
                {
                    "reason": reason,
                    "machine": state["machine"],
                }
            )

    machine_dependencies = []
    machine_by_id = {
        row["machine"]["id"]: row for row in machines
    }
    for key in sorted(machine_dependency_state):
        dependency = machine_dependency_state[key]
        machine_row = machine_by_id[dependency["machine"]["id"]]
        resolution = machine_row["form_resolution"]
        dependency["to_subproblem_id"] = resolution[
            "root_subproblem_id"
        ]
        dependency["status"] = (
            "resolved"
            if dependency["to_subproblem_id"] is not None
            else "max-machine-roots"
            if dependency["machine"]["id"] in root_limit_machine_ids
            else resolution["status"] + "-form"
            if resolution["status"] != "resolved"
            else "unseeded-form-root"
        )
        machine_dependencies.append(dependency)

    truncation_reasons = list(forest["truncation"]["reasons"])
    if root_limit_machine_ids:
        truncation_reasons.append("max-machine-roots")
    construction_truncation = {
        "truncated": bool(truncation_reasons),
        "reasons": truncation_reasons,
        "limits": {
            **options.limits(),
            "max_machine_roots": max_machine_roots,
        },
    }
    return {
        "status": (
            "answered"
            if machines
            else "no-machine-requirements"
        ),
        "assumptions": dict(MACHINE_CONSTRUCTION_ASSUMPTIONS),
        "machines": machines,
        "construction_forest": forest,
        "machine_dependency_edges": machine_dependencies,
        "bootstrapping_cycles": _construction_cycles(
            forest,
            machine_dependencies,
        ),
        "unresolved": unresolved,
        "truncation": construction_truncation,
    }


def _machine_construction_projection(
    reader: RuntimeGraphReader,
    prerequisites: dict[str, Any],
    *,
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
    options: ChainOptions,
    max_machine_roots: int,
) -> dict[str, Any]:
    projection = _machine_construction_payload(
        reader,
        prerequisites,
        options=options,
        max_machine_roots=max_machine_roots,
    )
    projection["evidence_ids"] = list(
        _add_runtime_projection_evidence(
            projection,
            scopes=scopes,
            snapshot_id=snapshot_id,
            evidence=evidence,
        )
    )
    return projection


def _infrastructure_operation_seeds(
    routes: dict[str, Any],
    machine_construction: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retain every bounded operation and its exact condition subjects.

    Infrastructure is a property of an operation in context, not merely of a
    machine name.  This handoff therefore keeps the complete material or
    construction route and separately identifies only the exact runtime nodes
    on which condition paths may be queried: the operation owner and every
    execution-proven machine.  It performs no infrastructure admission,
    voltage-tier derivation, or source interpretation.
    """

    if not isinstance(routes, dict) or not isinstance(
        routes.get("items"),
        list,
    ):
        raise CorpusBridgeError(
            "infrastructure handoff requires material-route items"
        )
    forest = (
        machine_construction.get("construction_forest")
        if isinstance(machine_construction, dict)
        else None
    )
    if not isinstance(forest, dict) or not isinstance(
        forest.get("items"),
        list,
    ):
        raise CorpusBridgeError(
            "infrastructure handoff requires construction-forest items"
        )

    result: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    sources = (
        ("material-route", routes["items"]),
        ("construction-route", forest["items"]),
    )
    for origin, operations in sources:
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                raise CorpusBridgeError(
                    f"{origin} infrastructure operation is not an object: "
                    f"{index}"
                )
            route_id = operation.get("id")
            subproblem_id = operation.get("subproblem_id")
            scope_row = operation.get("scope")
            producer = operation.get("producer")
            mechanics = operation.get("mechanics")
            boundaries = operation.get("boundaries")
            if (
                not isinstance(route_id, str)
                or not isinstance(subproblem_id, str)
                or not isinstance(scope_row, dict)
                or not isinstance(scope_row.get("profile"), str)
                or not isinstance(scope_row.get("physical_side"), str)
                or not isinstance(producer, dict)
                or producer.get("record_type") != "node"
                or not isinstance(producer.get("id"), str)
                or not isinstance(mechanics, dict)
                or not isinstance(mechanics.get("execution"), list)
                or not isinstance(boundaries, list)
            ):
                raise CorpusBridgeError(
                    f"{origin} infrastructure operation is incomplete: "
                    f"{index}"
                )
            identity = (origin, route_id, subproblem_id)
            if identity in identities:
                raise CorpusBridgeError(
                    "infrastructure handoff duplicates an operation: "
                    + ":".join(identity)
                )
            identities.add(identity)
            scope = Scope(
                str(scope_row["profile"]),
                str(scope_row["physical_side"]),
            )
            if _record_scope(producer) != scope:
                raise CorpusBridgeError(
                    "infrastructure operation owner escaped its route scope: "
                    + route_id
                )

            machines: dict[str, dict[str, Any]] = {}
            for binding_index, binding in enumerate(mechanics["execution"]):
                machine = (
                    binding.get("machine")
                    if isinstance(binding, dict)
                    else None
                )
                if (
                    not isinstance(machine, dict)
                    or machine.get("record_type") != "node"
                    or machine.get("kind") != "machine"
                    or not isinstance(machine.get("id"), str)
                    or _record_scope(machine) != scope
                ):
                    raise CorpusBridgeError(
                        "infrastructure execution binding lacks an exact "
                        f"same-scope machine: {route_id}[{binding_index}]"
                    )
                machine_id = str(machine["id"])
                subject = machines.get(machine_id)
                if subject is None:
                    machines[machine_id] = {
                        "role": "execution-machine",
                        "record": machine,
                        "execution_bindings": [binding],
                    }
                else:
                    if subject["record"] != machine:
                        raise CorpusBridgeError(
                            "infrastructure execution bindings disagree on "
                            "machine record: "
                            + machine_id
                        )
                    subject["execution_bindings"].append(binding)

            result.append(
                {
                    "origin": origin,
                    "operation": operation,
                    "condition_subjects": [
                        {
                            "role": "operation-owner",
                            "record": producer,
                        },
                        *[
                            machines[identifier]
                            for identifier in sorted(machines)
                        ],
                    ],
                }
            )

    result.sort(
        key=lambda row: (
            0 if row["origin"] == "material-route" else 1,
            row["operation"]["subproblem_id"],
            row["operation"]["id"],
        )
    )
    return result


def _infrastructure_candidate_payload(
    reader: RuntimeGraphReader,
    routes: dict[str, Any],
    machine_construction: dict[str, Any],
) -> dict[str, Any]:
    """Collect every exact condition candidate for every retained operation."""

    if not isinstance(reader, RuntimeGraphReader):
        raise CorpusBridgeError(
            "infrastructure candidate collection requires an open graph"
        )
    material_truncation = (
        routes.get("truncation") if isinstance(routes, dict) else None
    )
    construction_truncation = (
        machine_construction.get("truncation")
        if isinstance(machine_construction, dict)
        else None
    )
    if not isinstance(material_truncation, dict) or not isinstance(
        construction_truncation,
        dict,
    ):
        raise CorpusBridgeError(
            "infrastructure candidate collection requires both truncation "
            "states"
        )

    domain = RuntimeGraphDomainQuery(reader)
    operations: list[dict[str, Any]] = []
    unique_subject_ids: set[str] = set()
    unique_path_ids: set[str] = set()
    unique_predicate_path_ids: dict[str, set[str]] = defaultdict(set)
    path_count = 0
    path_kind_counts: dict[str, int] = defaultdict(int)
    predicate_counts: dict[str, int] = defaultdict(int)
    for seed in _infrastructure_operation_seeds(
        routes,
        machine_construction,
    ):
        operation = seed["operation"]
        scope = operation["scope"]
        subjects: list[dict[str, Any]] = []
        for subject in seed["condition_subjects"]:
            record = subject["record"]
            paths = list(
                domain.condition_candidate_paths(
                    ScopedTarget(
                        DomainProfileScope(
                            str(scope["profile"]),
                            str(scope["physical_side"]),
                        ),
                        record,
                    )
                )
            )
            subject_row = {
                **subject,
                "candidate_paths": paths,
            }
            subjects.append(subject_row)
            unique_subject_ids.add(str(record["id"]))
            path_count += len(paths)
            for path in paths:
                path_kind_counts[str(path["path_kind"])] += 1
                predicate = str(path["relationship"]["predicate"])
                relationship_id = path["relationship"].get("id")
                if not isinstance(relationship_id, str):
                    raise CorpusBridgeError(
                        "infrastructure candidate path lacks an exact "
                        "relationship ID"
                    )
                predicate_counts[predicate] += 1
                unique_path_ids.add(relationship_id)
                unique_predicate_path_ids[predicate].add(relationship_id)
        operations.append(
            {
                "origin": seed["origin"],
                "operation": operation,
                "condition_subjects": subjects,
            }
        )

    return {
        "status": "collected",
        "assumptions": dict(INFRASTRUCTURE_CANDIDATE_ASSUMPTIONS),
        "operations": operations,
        "summary": {
            "operation_count": len(operations),
            "unique_subject_count": len(unique_subject_ids),
            "candidate_path_count": path_count,
            "unique_candidate_path_count": len(unique_path_ids),
            "path_kind_counts": {
                key: path_kind_counts[key]
                for key in sorted(path_kind_counts)
            },
            "predicate_counts": {
                key: predicate_counts[key]
                for key in sorted(predicate_counts)
            },
            "unique_predicate_counts": {
                key: len(unique_predicate_path_ids[key])
                for key in sorted(unique_predicate_path_ids)
            },
        },
        "source_truncation": {
            "material_routes": material_truncation,
            "machine_construction": construction_truncation,
        },
    }


def _load_infrastructure_authority_registry(
    snapshot_id: str,
) -> dict[str, Any]:
    """Load and cross-bind the exact source authority used by admission."""

    try:
        source_lock_document = source_lock.load_source_lock()
        authority_registry = (
            infrastructure_source_authorities.load_authority_registry()
        )
        infrastructure_source_authorities.validate_authority_registry(
            authority_registry,
            source_lock_document,
        )
    except (
        source_lock.SourceLockError,
        infrastructure_source_authorities.InfrastructureSourceAuthorityError,
    ) as exc:
        raise CorpusBridgeError(
            f"infrastructure source authority is invalid: {exc}"
        ) from exc
    if authority_registry.get("snapshot_id") != snapshot_id:
        raise CorpusBridgeError(
            "infrastructure source authority belongs to a different snapshot"
        )
    return authority_registry


def _infrastructure_projection(
    reader: RuntimeGraphReader,
    routes: dict[str, Any],
    machine_construction: dict[str, Any],
    *,
    authority_registry: dict[str, Any],
    scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Normalize all candidates and retain a self-recomputable truth basis."""

    candidates = _infrastructure_candidate_payload(
        reader,
        routes,
        machine_construction,
    )
    try:
        projection = (
            infrastructure_projection.normalize_infrastructure_candidates(
                candidates,
                authority_registry,
            )
        )
        infrastructure_projection.validate_infrastructure_projection(
            projection,
            candidates,
            authority_registry,
        )
    except infrastructure_projection.InfrastructureProjectionError as exc:
        raise CorpusBridgeError(
            f"infrastructure admission failed closed: {exc}"
        ) from exc

    runtime_evidence_ids = _add_runtime_projection_evidence(
        candidates,
        scopes=scopes,
        snapshot_id=snapshot_id,
        evidence=evidence,
    )
    citations = {
        str(row["id"]): row
        for row in authority_registry["citations"]
    }
    authority_sets = {
        str(row["id"]): row
        for row in authority_registry["authority_sets"]
    }
    source_evidence_ids: list[str] = []
    for identifier in projection["used_source_authority_set_ids"]:
        record = authority_sets.get(identifier)
        if record is None:
            raise CorpusBridgeError(
                "infrastructure projection references an unknown source "
                f"authority set: {identifier}"
            )
        entry = _evidence(
            record,
            basis="pinned-source",
            authority="source-authority-registry",
            record_kind="source-authority-set",
        )
        _add_evidence(evidence, entry)
        source_evidence_ids.append(identifier)
    for identifier in projection["used_citation_ids"]:
        record = citations.get(identifier)
        if record is None:
            raise CorpusBridgeError(
                "infrastructure projection references an unknown source "
                f"citation: {identifier}"
            )
        entry = _evidence(
            record,
            basis="pinned-source",
            authority="source-authority-registry",
            record_kind="source-citation",
        )
        _add_evidence(evidence, entry)
        source_evidence_ids.append(identifier)

    projection["candidate_basis"] = candidates
    projection["source_authority_registry"] = authority_registry
    projection["evidence_ids"] = sorted(
        set(runtime_evidence_ids) | set(source_evidence_ids)
    )
    try:
        infrastructure_projection.validate_infrastructure_projection(
            projection,
            candidates,
            authority_registry,
        )
    except infrastructure_projection.InfrastructureProjectionError as exc:
        raise CorpusBridgeError(
            f"infrastructure projection recomputation failed: {exc}"
        ) from exc
    return projection


def _infrastructure_authority_projection(
    projection: dict[str, Any],
) -> dict[str, Any]:
    registry = projection["source_authority_registry"]
    authority_ids = set(projection["used_source_authority_set_ids"])
    citation_ids = set(projection["used_citation_ids"])
    return {
        "registry_id": registry["registry_id"],
        "authority_sets": [
            row
            for row in registry["authority_sets"]
            if row["id"] in authority_ids
        ],
        "citations": [
            row
            for row in registry["citations"]
            if row["id"] in citation_ids
        ],
        "evidence_ids": sorted(authority_ids | citation_ids),
    }


def _confidence_projection(
    *,
    question: dict[str, Any],
    resolved: bool,
    ambiguous: bool,
    runtime_evidence_ids: Sequence[str],
    curated_evidence_ids: Sequence[str],
    quest_evidence_ids: Sequence[str],
    profile_scope: list[dict[str, str]],
    snapshot_id: str,
    known_gaps: Sequence[dict[str, str]],
    missing_required_bases: Sequence[str],
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """State exactly what this snapshot/profile answer verifies and omits."""

    target_ids = tuple(
        sorted(set(runtime_evidence_ids) | set(curated_evidence_ids))
    )
    quest_ids = tuple(sorted(set(quest_evidence_ids)))
    required_bases = tuple(sorted(question["required_evidence_bases"]))
    required_basis_ids = tuple(
        sorted(
            identifier
            for identifier, row in evidence.items()
            if row["basis"] in required_bases
        )
    )
    all_ids = tuple(
        sorted(set(target_ids) | set(quest_ids) | set(required_basis_ids))
    )
    missing_profiles = [
        f"{row['profile']}/{row['physical_side']}"
        for row in profile_scope
        if row["availability"] == "missing"
    ]
    checks = [
        {
            "name": "exact-identity",
            "status": (
                "passed"
                if resolved and not ambiguous
                else "failed"
            ),
            "detail": (
                "canonical and runtime identities resolve exactly"
                if resolved and not ambiguous
                else "canonical or runtime identity is unresolved or ambiguous"
            ),
            "evidence_ids": list(target_ids),
        },
        {
            "name": "snapshot-bound",
            "status": "passed" if all_ids else "failed",
            "detail": (
                f"all cited records are bound to {snapshot_id}"
                if all_ids
                else "no evidence records are available to bind the snapshot"
            ),
            "evidence_ids": list(all_ids),
        },
        {
            "name": "profile-coverage",
            "status": "partial" if missing_profiles else "passed",
            "detail": (
                "missing exact runtime target in: "
                + ", ".join(missing_profiles)
                if missing_profiles
                else "every requested profile/side was consulted"
            ),
            "evidence_ids": list(runtime_evidence_ids),
        },
        {
            "name": "required-evidence-bases",
            "status": "failed" if missing_required_bases else "passed",
            "detail": (
                "missing required evidence bases: "
                + ", ".join(missing_required_bases)
                if missing_required_bases
                else "all question-required evidence bases are present"
            ),
            "evidence_ids": list(required_basis_ids),
        },
        {
            "name": "quest-guidance-link",
            "status": "passed" if quest_ids else "not-evaluated",
            "detail": (
                "exact quest guidance is linked to the runtime identity"
                if quest_ids
                else "no exact quest guidance link is available"
            ),
            "evidence_ids": list(quest_ids),
        },
    ]
    if (
        not resolved
        or ambiguous
        or bool(missing_required_bases)
        or not all_ids
    ):
        status = "unresolved"
    elif known_gaps:
        status = "verified-with-known-gaps"
    else:
        status = "verified"
    observed_bases = sorted(
        {evidence[identifier]["basis"] for identifier in all_ids}
    )
    return {
        "status": status,
        "scope_statement": (
            "verified only for the named snapshot and requested profile/side "
            "entries; no cross-version or global validity is claimed"
        ),
        "snapshot_id": snapshot_id,
        "profile_scope": profile_scope,
        "evidence_bases": {
            "required": list(required_bases),
            "observed": observed_bases,
            "missing": list(missing_required_bases),
        },
        "checks": checks,
        "known_gap_codes": sorted(
            {row["code"] for row in known_gaps}
        ),
        "boundaries": {
            "global_validity_claimed": False,
            "profile_comparison_evaluated": False,
            "mechanical_route_evaluated": False,
            "quest_prerequisites_are_mechanical": False,
        },
        "evidence_ids": list(all_ids),
    }


def _exact_catalog_candidates(
    entities: Sequence[dict[str, Any]],
    selector_kind: str,
    key: str,
    snapshot_id: str,
) -> tuple[dict[str, Any], ...]:
    catalog_kind = CATALOG_KINDS.get(selector_kind)
    if catalog_kind is None:
        return ()
    matches = [
        row
        for row in entities
        if row["kind"] == catalog_kind
        and row["snapshot_id"] == snapshot_id
        and (row["id"] == key or key in row["aliases"])
    ]
    return tuple(sorted(matches, key=lambda row: row["id"]))


def _curated_basis(
    record: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
) -> str:
    identifier = str(record.get("id", ""))
    if identifier.startswith("CLAIM-"):
        evidence = record.get("evidence")
        if isinstance(evidence, dict) and evidence.get("basis") == "pinned-source":
            return "pinned-source"
        return "curated-interpretation"
    if identifier.startswith("REL-"):
        claim_id = record.get("claim_id")
        if isinstance(claim_id, str):
            claim = claims_by_id.get(claim_id)
            if claim is not None:
                return _curated_basis(claim, claims_by_id)
        return "curated-interpretation"
    provenance = record.get("provenance")
    return (
        "pinned-source"
        if isinstance(provenance, list) and bool(provenance)
        else "curated-interpretation"
    )


def _evidence(
    record: dict[str, Any],
    *,
    basis: str,
    authority: str,
    record_kind: str,
) -> dict[str, Any]:
    identifier = record.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise CorpusBridgeError("evidence record has no stable id")
    return {
        "id": identifier,
        "basis": basis,
        "authority": authority,
        "record_kind": record_kind,
        "record": record,
    }


def _add_evidence(
    registry: dict[str, dict[str, Any]],
    entry: dict[str, Any],
) -> None:
    previous = registry.get(entry["id"])
    if previous is not None and previous != entry:
        raise CorpusBridgeError(
            f"evidence id resolves to different authority records: {entry['id']}"
        )
    registry[entry["id"]] = entry


def _curated_projection(
    canonical: dict[str, Any] | None,
    catalogs: dict[str, list[dict[str, Any]]],
    evidence: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    tuple[str, ...],
]:
    if canonical is None:
        unresolved = {"status": "unresolved", "items": [], "evidence_ids": []}
        return unresolved, unresolved, unresolved, ()

    entities = {row["id"]: row for row in catalogs["entities.jsonl"]}
    claims = {
        row["id"]: row for row in catalogs["claims.jsonl"]
    }
    subject_claims = sorted(
        (
            row
            for row in claims.values()
            if row["subject"] == canonical["id"]
            and row["snapshot_id"] == canonical["snapshot_id"]
        ),
        key=lambda row: row["id"],
    )
    relations = sorted(
        (
            row
            for row in catalogs["relations.jsonl"]
            if canonical["id"] in {row["subject"], row["object"]}
            and row["snapshot_id"] == canonical["snapshot_id"]
        ),
        key=lambda row: row["id"],
    )
    source_ids: set[str] = set()
    for claim in subject_claims:
        source_ids.update(claim["source_refs"])
    for relation in relations:
        other = (
            relation["object"]
            if relation["subject"] == canonical["id"]
            else relation["subject"]
        )
        entity = entities.get(other)
        if entity is not None and entity["kind"] == "source":
            source_ids.add(other)
    sources = [
        entities[identifier]
        for identifier in sorted(source_ids)
        if identifier in entities
        and entities[identifier]["snapshot_id"] == canonical["snapshot_id"]
    ]

    curated_records: list[tuple[dict[str, Any], str]] = [
        (canonical, "curated-entity"),
        *((row, "curated-claim") for row in subject_claims),
        *((row, "curated-relation") for row in relations),
        *((row, "curated-entity") for row in sources),
    ]
    curated_ids: set[str] = set()
    for record, record_kind in curated_records:
        entry = _evidence(
            record,
            basis=_curated_basis(record, claims),
            authority="curated-catalog",
            record_kind=record_kind,
        )
        _add_evidence(evidence, entry)
        curated_ids.add(entry["id"])

    def relation_rows(predicate: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for relation in relations:
            if relation["predicate"] != predicate or relation["object"] != canonical["id"]:
                continue
            source = entities.get(relation["subject"])
            if source is None:
                continue
            claim = (
                claims.get(relation["claim_id"])
                if isinstance(relation.get("claim_id"), str)
                else None
            )
            row_ids = [relation["id"], source["id"], canonical["id"]]
            if claim is not None:
                row_ids.append(claim["id"])
            result.append(
                {
                    "relationship": relation,
                    "source": source,
                    "claim": claim,
                    "evidence_ids": sorted(set(row_ids)),
                }
            )
        return result

    declaration_items = relation_rows("declares")
    ownership_items = relation_rows("owns")
    declaration = {
        "status": "answered" if declaration_items else "unresolved",
        "items": declaration_items,
        "evidence_ids": sorted(
            {
                identifier
                for row in declaration_items
                for identifier in row["evidence_ids"]
            }
        ),
    }
    ownership = {
        "status": "answered" if ownership_items else "unresolved",
        "items": ownership_items,
        "evidence_ids": sorted(
            {
                identifier
                for row in ownership_items
                for identifier in row["evidence_ids"]
            }
        ),
    }
    pinned_source = {
        "status": "answered" if curated_ids else "unresolved",
        "entity": canonical,
        "claims": subject_claims,
        "relations": relations,
        "sources": sources,
        "evidence_ids": sorted(curated_ids),
    }
    return pinned_source, declaration, ownership, tuple(sorted(curated_ids))


def _quest_projection(
    reader: RuntimeGraphReader,
    canonical_id: str | None,
    material_nodes: Sequence[dict[str, Any]],
    links: Sequence[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    requested_scopes: tuple[Scope, ...],
    snapshot_id: str,
    evidence: dict[str, dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    tuple[str, ...],
    tuple[str, ...],
]:
    if canonical_id is None:
        return [], [], (), ()
    scope_values = {scope.link_value for scope in requested_scopes}
    direct_links = [
        row
        for row in links
        if row["canonical_entity_id"] == canonical_id
        and row["observed_kind"] in {"quest-node", "quest-task", "quest-reward"}
        and scope_values.intersection(row["profile_scope"])
    ]
    direct_links.sort(key=lambda row: row["id"])
    direct_records: list[
        tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]
    ] = []
    expected_kinds = {
        "quest-node": "quest",
        "quest-task": "task",
        "quest-reward": "reward",
    }
    for link in direct_links:
        record = nodes.get(link["observed_id"])
        if record is None or record["kind"] != expected_kinds[link["observed_kind"]]:
            raise CorpusBridgeError(
                f"quest corpus link does not resolve to its declared node kind: {link['id']}"
            )
        attributes = record.get("attributes")
        if not isinstance(attributes, dict) or (
            attributes.get("key_kind") != link["key_kind"]
            or attributes.get("key_value") != link["key_value"]
        ):
            raise CorpusBridgeError(
                f"quest corpus link typed key differs from the observed node: {link['id']}"
            )
        runtime_basis = _quest_runtime_basis(
            reader,
            link,
            material_nodes,
            requested_scopes,
            snapshot_id,
            evidence,
        )
        direct_records.append((link, record, runtime_basis))

    incoming: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        incoming[(edge["kind"], edge["object"])].append(edge)
        outgoing[(edge["kind"], edge["subject"])].append(edge)

    quest_ids: set[str] = set()
    direct_by_quest: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link, record, runtime_basis in direct_records:
        match = {
            "link": link,
            "record": record,
            "runtime_basis": runtime_basis,
        }
        if record["kind"] == "quest":
            quest_ids.add(record["id"])
            direct_by_quest[record["id"]].append(match)
        else:
            owner_kind = "has_task" if record["kind"] == "task" else "has_reward"
            owner_edges = incoming[(owner_kind, record["id"])]
            if not owner_edges:
                raise CorpusBridgeError(
                    f"linked quest {record['kind']} has no owning quest: {link['id']}"
                )
            for edge in owner_edges:
                owner = nodes[edge["subject"]]
                if owner["kind"] != "quest":
                    raise CorpusBridgeError(
                        f"linked quest {record['kind']} has a non-quest owner: {edge['id']}"
                    )
                quest_ids.add(edge["subject"])
                direct_by_quest[edge["subject"]].append(
                    {**match, "relationship": edge}
                )

    quest_rows: list[dict[str, Any]] = []
    prerequisite_by_relationship: dict[str, dict[str, Any]] = {}
    quest_graph_evidence_ids: set[str] = set()
    runtime_basis_evidence_ids: set[str] = set()
    for quest_id in sorted(quest_ids):
        quest = nodes[quest_id]
        row_ids = {quest_id}
        for via in direct_by_quest[quest_id]:
            row_ids.add(via["record"]["id"])
            for basis in via["runtime_basis"]:
                runtime_basis_evidence_ids.update(basis["evidence_ids"])
                row_ids.update(basis["evidence_ids"])
            relationship = via.get("relationship")
            if isinstance(relationship, dict):
                row_ids.add(relationship["id"])
        line_memberships: list[dict[str, Any]] = []
        for relationship in sorted(
            outgoing[("belongs_to_line", quest_id)],
            key=lambda row: (row["object"], row["id"]),
        ):
            quest_line = nodes[relationship["object"]]
            if quest_line["kind"] != "quest_line":
                raise CorpusBridgeError(
                    f"quest line relationship targets a non-line: {relationship['id']}"
                )
            line_memberships.append(
                {
                    "relationship": relationship,
                    "quest_line": quest_line,
                    "evidence_ids": [
                        quest_id,
                        relationship["id"],
                        quest_line["id"],
                    ],
                }
            )
            row_ids.update(
                (relationship["id"], quest_line["id"])
            )
        quest_rows.append(
            {
                "quest": quest,
                "matches": sorted(
                    direct_by_quest[quest_id],
                    key=lambda row: (
                        row["record"]["id"],
                        row["link"]["id"],
                    ),
                ),
                "quest_lines": line_memberships,
                "evidence_ids": sorted(row_ids),
            }
        )
        quest_graph_evidence_ids.update(
            identifier
            for identifier in row_ids
            if identifier.startswith("qg")
        )
        requires_relationships = {
            relationship["id"]: relationship
            for relationship in (
                outgoing[("requires_quest", quest_id)]
                + incoming[("requires_quest", quest_id)]
            )
        }
        for relationship in sorted(
            requires_relationships.values(),
            key=lambda row: (
                row["subject"],
                row["object"],
                row["id"],
            ),
        ):
            dependent = nodes[relationship["subject"]]
            prerequisite = nodes[relationship["object"]]
            if dependent["kind"] != "quest" or prerequisite["kind"] != "quest":
                raise CorpusBridgeError(
                    f"quest prerequisite edge has a non-quest endpoint: {relationship['id']}"
                )
            projected = {
                "quest": dependent,
                "dependent_quest": dependent,
                "relationship": relationship,
                "prerequisite": prerequisite,
                "semantics": "quest-ordering-only",
                "evidence_ids": [
                    dependent["id"],
                    relationship["id"],
                    prerequisite["id"],
                ],
            }
            previous = prerequisite_by_relationship.get(relationship["id"])
            if previous is not None and previous != projected:
                raise CorpusBridgeError(
                    "quest prerequisite relationship projects inconsistently: "
                    + relationship["id"]
                )
            prerequisite_by_relationship[relationship["id"]] = projected
            quest_graph_evidence_ids.update(
                (
                    dependent["id"],
                    relationship["id"],
                    prerequisite["id"],
                )
            )

    prerequisite_rows = [
        prerequisite_by_relationship[identifier]
        for identifier in sorted(
            prerequisite_by_relationship,
            key=lambda identifier: (
                prerequisite_by_relationship[identifier]["relationship"]["subject"],
                prerequisite_by_relationship[identifier]["relationship"]["object"],
                identifier,
            ),
        )
    ]
    evidence_records: dict[str, tuple[dict[str, Any], str]] = {}
    for identifier in quest_graph_evidence_ids:
        record = nodes.get(identifier)
        record_kind = "quest-node"
        if record is None:
            record = next(
                (edge for edge in edges if edge["id"] == identifier),
                None,
            )
            record_kind = "quest-edge"
        if record is None:
            raise CorpusBridgeError(
                f"quest projection references an unknown record: {identifier}"
            )
        evidence_records[identifier] = (record, record_kind)
    for identifier in sorted(evidence_records):
        record, record_kind = evidence_records[identifier]
        _add_evidence(
            evidence,
            _evidence(
                record,
                basis="quest-data",
                authority="quest-graph",
                record_kind=record_kind,
            ),
        )
    return (
        quest_rows,
        prerequisite_rows,
        tuple(
            sorted(
                quest_graph_evidence_ids
                | runtime_basis_evidence_ids
            )
        ),
        tuple(link["id"] for link in direct_links),
    )


def _has_path(value: dict[str, Any], dotted: str) -> bool:
    current: Any = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def validate_answer(
    answer: dict[str, Any],
    question: dict[str, Any],
) -> None:
    """Validate the schema plus the authority/evidence join invariants."""

    _validate_schema(answer, _schema(ANSWER_SCHEMA), "corpus answer")
    if answer["question_id"] != question["id"] or answer["query"] != question["query"]:
        raise CorpusBridgeError("answer identity differs from its question")
    missing = sorted(
        field
        for field in question["required_fields"]
        if not _has_path(answer, field)
    )
    if missing:
        raise CorpusBridgeError(
            f"answer omits required question fields: {missing}"
        )
    evidence_rows = answer["evidence"]
    by_id = {row["id"]: row for row in evidence_rows}
    if len(by_id) != len(evidence_rows):
        raise CorpusBridgeError("answer evidence ids are duplicated")
    referenced: set[str] = set()
    for assertion in answer["assertions"]:
        identifiers = assertion["evidence_ids"]
        if assertion["status"] == "supported" and not identifiers:
            raise CorpusBridgeError(
                f"supported assertion has no evidence: {assertion['path']}"
            )
        missing_ids = sorted(set(identifiers) - set(by_id))
        if missing_ids:
            raise CorpusBridgeError(
                f"assertion references missing evidence: {missing_ids}"
            )
        referenced.update(identifiers)
    unused = sorted(set(by_id) - referenced)
    if unused:
        raise CorpusBridgeError(
            f"answer contains unreferenced evidence padding: {unused}"
        )
    for row in evidence_rows:
        allowed = {
            "normalized-runtime-graph": {
                "runtime-mechanics",
                "runtime-presentation",
            },
            "curated-catalog": {
                "pinned-source",
                "curated-interpretation",
            },
            "quest-graph": {"quest-data"},
            "source-authority-registry": {"pinned-source"},
        }[row["authority"]]
        if row["basis"] not in allowed:
            raise CorpusBridgeError(
                f"evidence basis collapses authority for {row['id']}"
            )
        if row["record"].get("id") != row["id"]:
            raise CorpusBridgeError(
                f"evidence id differs from its original record: {row['id']}"
            )

    def require_projected_record(
        record: Any,
        *,
        authority: str,
        record_kind: str,
        context: str,
    ) -> None:
        if not isinstance(record, dict):
            raise CorpusBridgeError(
                f"projected evidence record is not an object: {context}"
            )
        identifier = record.get("id")
        if not isinstance(identifier, str) or identifier not in by_id:
            raise CorpusBridgeError(
                f"projected evidence record is missing from evidence: {context}"
            )
        entry = by_id[identifier]
        if (
            entry["authority"] != authority
            or entry["record_kind"] != record_kind
            or entry["record"] != record
        ):
            raise CorpusBridgeError(
                f"projected evidence record differs from cited evidence: {context}"
            )

    for index, record in enumerate(answer["runtime"]["resolved"]):
        require_projected_record(
            record,
            authority="normalized-runtime-graph",
            record_kind="runtime-node",
            context=f"runtime.resolved[{index}]",
        )

    projection_scopes = tuple(
        Scope(row["profile"], row["physical_side"])
        for row in answer["profile_scope"]
    )
    for section_name in (
        "producers",
        "consumers",
        "routes",
        "recipes",
        "executable_machines",
        "ingredient_slots",
        "reusable_requirements",
        "byproducts",
        "recycling_paths",
        "prerequisites",
        "machine_construction",
        "classification",
        "comparison",
    ):
        section = answer.get(section_name)
        if section is None:
            continue
        records = _validated_runtime_projection_records(
            section,
            scopes=projection_scopes,
            snapshot_id=answer["snapshot_id"],
        )
        evidence_ids = section.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            raise CorpusBridgeError(
                f"{section_name}.evidence_ids must be an array"
            )
        if set(evidence_ids) != set(records):
            raise CorpusBridgeError(
                f"{section_name} runtime evidence closure differs: "
                f"missing={sorted(set(records) - set(evidence_ids))} "
                f"padding={sorted(set(evidence_ids) - set(records))}"
            )
        for identifier, record in records.items():
            entry = by_id.get(identifier)
            expected_kind = (
                "runtime-edge"
                if record.get("record_type") == "edge"
                else "runtime-node"
            )
            if (
                entry is None
                or entry["authority"] != "normalized-runtime-graph"
                or entry["record_kind"] != expected_kind
                or entry["record"] != record
            ):
                raise CorpusBridgeError(
                    f"{section_name} runtime record differs from evidence: "
                    + identifier
                )
        if section_name == "machine_construction" and any(
            by_id[identifier]["basis"] != "runtime-mechanics"
            for identifier in records
        ):
            raise CorpusBridgeError(
                "machine construction cites non-mechanical evidence"
            )

    infrastructure_requirements = answer.get(
        "infrastructure_requirements"
    )
    if infrastructure_requirements is not None:
        candidate_basis = infrastructure_requirements.get(
            "candidate_basis"
        )
        authority_registry = infrastructure_requirements.get(
            "source_authority_registry"
        )
        if not isinstance(candidate_basis, dict) or not isinstance(
            authority_registry,
            dict,
        ):
            raise CorpusBridgeError(
                "infrastructure requirements omit their recomputation basis"
            )
        try:
            source_lock_document = source_lock.load_source_lock()
            infrastructure_source_authorities.validate_authority_registry(
                authority_registry,
                source_lock_document,
            )
            infrastructure_projection.validate_infrastructure_projection(
                infrastructure_requirements,
                candidate_basis,
                authority_registry,
            )
        except (
            source_lock.SourceLockError,
            infrastructure_source_authorities.InfrastructureSourceAuthorityError,
            infrastructure_projection.InfrastructureProjectionError,
        ) as exc:
            raise CorpusBridgeError(
                f"infrastructure semantic recomputation failed: {exc}"
            ) from exc

        records = _validated_runtime_projection_records(
            candidate_basis,
            scopes=projection_scopes,
            snapshot_id=answer["snapshot_id"],
        )
        citation_by_id = {
            str(row["id"]): row
            for row in authority_registry["citations"]
        }
        authority_by_id = {
            str(row["id"]): row
            for row in authority_registry["authority_sets"]
        }
        used_citation_ids = set(
            infrastructure_requirements["used_citation_ids"]
        )
        used_authority_ids = set(
            infrastructure_requirements[
                "used_source_authority_set_ids"
            ]
        )
        expected_evidence_ids = (
            set(records) | used_citation_ids | used_authority_ids
        )
        if set(infrastructure_requirements["evidence_ids"]) != (
            expected_evidence_ids
        ):
            raise CorpusBridgeError(
                "infrastructure evidence closure differs from its exact "
                "candidate and source basis"
            )
        for identifier, record in records.items():
            entry = by_id.get(identifier)
            expected_kind = (
                "runtime-edge"
                if record.get("record_type") == "edge"
                else "runtime-node"
            )
            if (
                entry is None
                or entry["authority"] != "normalized-runtime-graph"
                or entry["record_kind"] != expected_kind
                or entry["basis"] != _runtime_evidence_basis(record)
                or entry["record"] != record
            ):
                raise CorpusBridgeError(
                    "infrastructure runtime evidence differs: "
                    + identifier
                )
        for (
            identifiers,
            source_rows,
            record_kind,
        ) in (
            (
                used_citation_ids,
                citation_by_id,
                "source-citation",
            ),
            (
                used_authority_ids,
                authority_by_id,
                "source-authority-set",
            ),
        ):
            if not identifiers.issubset(source_rows):
                raise CorpusBridgeError(
                    "infrastructure projection references unknown source "
                    "authority evidence"
                )
            for identifier in identifiers:
                entry = by_id.get(identifier)
                if (
                    entry is None
                    or entry["basis"] != "pinned-source"
                    or entry["authority"]
                    != "source-authority-registry"
                    or entry["record_kind"] != record_kind
                    or entry["record"] != source_rows[identifier]
                ):
                    raise CorpusBridgeError(
                        "infrastructure source evidence differs: "
                        + identifier
                    )

    recipes = answer.get("recipes")
    executable_machines = answer.get("executable_machines")
    if executable_machines is not None:
        if not isinstance(recipes, dict):
            raise CorpusBridgeError(
                "executable_machines requires the recipes projection"
            )
        expected_machine_items = [
            {
                "scope": row["scope"],
                "recipe": row["recipe"],
                "classification": row["mechanics"]["classification"],
                "machines": row["mechanics"]["execution"],
            }
            for row in recipes["items"]
        ]
        if executable_machines["items"] != expected_machine_items:
            raise CorpusBridgeError(
                "executable_machines differs from recipe execution mechanics"
            )
        expected_without = [
            row["recipe"]["id"]
            for row in expected_machine_items
            if not row["machines"]
        ]
        if (
            executable_machines["without_executable_machine"]
            != expected_without
        ):
            raise CorpusBridgeError(
                "executable_machines unresolved list differs from recipes"
            )

    routes = answer.get("routes")
    for section_name in (
        "ingredient_slots",
        "reusable_requirements",
    ):
        section = answer.get(section_name)
        if section is None:
            continue
        if not isinstance(routes, dict):
            raise CorpusBridgeError(
                f"{section_name} requires the routes projection"
            )
        if (
            section["status"] != routes["status"]
            or section["target"] != routes["target"]
            or section["truncation"] != routes["truncation"]
        ):
            raise CorpusBridgeError(
                f"{section_name} scope or truncation differs from routes"
            )
        expected_items = [
            {
                "route_id": route["id"],
                "subproblem_id": route["subproblem_id"],
                "scope": route["scope"],
                "producer": route["producer"],
                "mode": route[section_name]["mode"],
                "slots": route[section_name]["slots"],
            }
            for route in routes["items"]
        ]
        if section["items"] != expected_items:
            raise CorpusBridgeError(
                f"{section_name} differs from its route projection"
            )

    byproducts = answer.get("byproducts")
    if byproducts is not None:
        if not isinstance(routes, dict):
            raise CorpusBridgeError(
                "byproducts requires the routes projection"
            )
        expected_byproducts = _byproduct_payload(routes)
        observed_byproducts = {
            key: value
            for key, value in byproducts.items()
            if key != "evidence_ids"
        }
        if observed_byproducts != expected_byproducts:
            raise CorpusBridgeError(
                "byproducts differs from its lossless route projection"
            )

    recycling_paths = answer.get("recycling_paths")
    if recycling_paths is not None:
        if not isinstance(routes, dict) or not isinstance(byproducts, dict):
            raise CorpusBridgeError(
                "recycling_paths requires routes and byproducts"
            )
        try:
            expected_roots = canonical_recycling_roots(
                _recycling_root_seeds(byproducts)
            )
            structural = {
                key: value
                for key, value in recycling_paths.items()
                if key not in {"status", "semantics", "evidence_ids"}
            }
            validate_recycling_result(structural, expected_roots)
        except RuntimeGraphQueryError as exc:
            raise CorpusBridgeError(
                f"recycling_paths semantic recomputation failed: {exc}"
            ) from exc
        expected_status = _recycling_status(
            byproducts,
            len(expected_roots),
        )
        if recycling_paths.get("status") != expected_status:
            raise CorpusBridgeError(
                "recycling_paths status differs from its exact roots"
            )
        if recycling_paths.get("semantics") != RECYCLING_SEMANTICS:
            raise CorpusBridgeError(
                "recycling_paths semantics differ from policy"
            )

        final_targets = {
            (
                target.scope.profile,
                target.scope.physical_side,
                target.node["id"],
            )
            for target in _recycling_final_targets(routes)
        }
        retained_targets = {
            (
                target.subproblem_id,
                target.target.scope.profile,
                target.target.scope.physical_side,
                target.target.node["id"],
            )
            for target in _recycling_route_targets(routes)
        }
        for closure in recycling_paths["closures"]:
            classification = closure["classification"]
            if classification not in {
                "returns-final-target",
                "rejoins-retained-route",
                "forward-cycle",
            }:
                continue
            if classification == "forward-cycle":
                continue
            destination_scope = closure.get("destination_scope")
            destination_target = closure.get("destination_target")
            if (
                not isinstance(destination_scope, dict)
                or not isinstance(destination_target, dict)
            ):
                raise CorpusBridgeError(
                    "closed recycling path omits its destination"
                )
            destination_key = (
                destination_scope.get("profile"),
                destination_scope.get("physical_side"),
                destination_target.get("id"),
            )
            if (
                classification == "returns-final-target"
                and destination_key not in final_targets
            ):
                raise CorpusBridgeError(
                    "recycling final-target closure differs from routes"
                )
            if classification == "rejoins-retained-route":
                route_key = (
                    closure.get("route_subproblem_id"),
                    *destination_key,
                )
                if route_key not in retained_targets:
                    raise CorpusBridgeError(
                        "recycling route closure differs from routes"
                    )
            match = closure.get("match")
            path = match.get("path") if isinstance(match, dict) else None
            if (
                not isinstance(match, dict)
                or not isinstance(match.get("role"), str)
                or not match["role"]
                or not isinstance(path, list)
                or any(
                    not isinstance(edge, dict)
                    or edge.get("record_type") != "edge"
                    for edge in path
                )
            ):
                raise CorpusBridgeError(
                    "closed recycling path has no exact match path"
                )
            current_id = destination_target["id"]
            for edge in path:
                if edge.get("subject") == current_id:
                    current_id = edge.get("object")
                elif edge.get("object") == current_id:
                    current_id = edge.get("subject")
                else:
                    raise CorpusBridgeError(
                        "recycling closure match path is disconnected"
                    )
            target = closure.get("target")
            if (
                not isinstance(target, dict)
                or current_id != target.get("id")
            ):
                raise CorpusBridgeError(
                    "recycling closure match path does not reach its output"
                )

    route_prerequisites = answer.get("prerequisites")
    if route_prerequisites is not None:
        if not isinstance(routes, dict):
            raise CorpusBridgeError(
                "prerequisites requires the routes projection"
            )
        expected_prerequisites = _prerequisite_payload(routes)
        observed_prerequisites = {
            key: value
            for key, value in route_prerequisites.items()
            if key != "evidence_ids"
        }
        if observed_prerequisites != expected_prerequisites:
            raise CorpusBridgeError(
                "prerequisites differs from its route DAG projection"
            )

    machine_construction = answer.get("machine_construction")
    if machine_construction is not None:
        if not isinstance(route_prerequisites, dict):
            raise CorpusBridgeError(
                "machine_construction requires prerequisites"
            )
        initial_seeds = _machine_construction_seeds(
            route_prerequisites
        )
        initial_by_id = {
            row["machine"]["id"]: row for row in initial_seeds
        }
        machines = machine_construction["machines"]
        machine_by_id = {
            row["machine"]["id"]: row for row in machines
        }
        if (
            len(machine_by_id) != len(machines)
            or list(machine_by_id) != sorted(machine_by_id)
            or not set(initial_by_id).issubset(machine_by_id)
        ):
            raise CorpusBridgeError(
                "machine_construction machines are missing, duplicated, "
                "or unordered"
            )
        forest = machine_construction["construction_forest"]
        subproblem_by_id = {
            row["id"]: row for row in forest["subproblems"]
        }
        route_by_id = {
            row["id"]: row for row in forest["items"]
        }
        if (
            len(subproblem_by_id) != len(forest["subproblems"])
            or len(route_by_id) != len(forest["items"])
        ):
            raise CorpusBridgeError(
                "machine construction forest duplicates route or "
                "subproblem identities"
            )
        root_ids = set(forest["roots"])
        if not root_ids.issubset(subproblem_by_id):
            raise CorpusBridgeError(
                "machine construction root does not name a subproblem"
            )
        expected_forest_status = (
            "answered"
            if forest["items"]
            else "no-mechanical-route"
        )
        if forest["status"] != expected_forest_status:
            raise CorpusBridgeError(
                "machine construction forest status differs from routes"
            )
        if (
            machine_construction["status"]
            != ("answered" if machines else "no-machine-requirements")
            or machine_construction["assumptions"]
            != MACHINE_CONSTRUCTION_ASSUMPTIONS
        ):
            raise CorpusBridgeError(
                "machine construction status or assumptions drifted"
            )

        construction_requirements: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        for machine_id, row in machine_by_id.items():
            if row["machine"]["id"] != machine_id:
                raise CorpusBridgeError(
                    "machine construction machine identity drifted"
                )
            required_by = row["required_by"]
            expected_order = sorted(
                required_by,
                key=lambda item: (
                    item["source"],
                    item["route_id"],
                    item["subproblem_id"],
                ),
            )
            if required_by != expected_order or len(
                {
                    (
                        item["source"],
                        item["route_id"],
                        item["subproblem_id"],
                    )
                    for item in required_by
                }
            ) != len(required_by):
                raise CorpusBridgeError(
                    "machine construction provenance is duplicated or "
                    "unordered"
                )
            expected_origins = sorted(
                {item["source"] for item in required_by}
            )
            if row["origins"] != expected_origins:
                raise CorpusBridgeError(
                    "machine construction origins differ from provenance"
                )
            initial = initial_by_id.get(machine_id)
            if initial is not None:
                if row["machine"] != initial["machine"]:
                    raise CorpusBridgeError(
                        "machine construction initial machine record drifted"
                    )
                observed_material = [
                    item
                    for item in required_by
                    if item["source"] == "material-route"
                ]
                if observed_material != initial["required_by"]:
                    raise CorpusBridgeError(
                        "machine construction material-route provenance "
                        "differs from prerequisites"
                    )
            elif any(
                item["source"] == "material-route"
                for item in required_by
            ):
                raise CorpusBridgeError(
                    "machine construction invents material-route provenance"
                )
            for item in required_by:
                if item["source"] == "construction-route":
                    construction_requirements[
                        (
                            item["route_id"],
                            item["subproblem_id"],
                            machine_id,
                        )
                    ] = item

            resolution = row["form_resolution"]
            admitted = []
            for path in resolution["paths"]:
                if path["machine"] != row["machine"]:
                    raise CorpusBridgeError(
                        "machine form path names a different machine"
                    )
                expected_admission = _machine_form_path_admission(path)
                if path["admission"] != expected_admission:
                    raise CorpusBridgeError(
                        "machine form admission differs from policy"
                    )
                if expected_admission == "admitted":
                    admitted.append(path)
            expected_resolution_status = (
                "resolved"
                if len(admitted) == 1
                else "ambiguous"
                if len(admitted) > 1
                else "unresolved"
            )
            expected_selected = (
                admitted[0]["relationship"]["id"]
                if len(admitted) == 1
                else None
            )
            if (
                resolution["status"] != expected_resolution_status
                or resolution["selected_relationship_id"]
                != expected_selected
            ):
                raise CorpusBridgeError(
                    "machine form resolution differs from exact paths"
                )
            root_id = resolution["root_subproblem_id"]
            if root_id is not None:
                selected_form = admitted[0]["form"]
                root = subproblem_by_id.get(root_id)
                if (
                    root_id not in root_ids
                    or root is None
                    or root["target"] != selected_form
                ):
                    raise CorpusBridgeError(
                        "machine form root differs from its selected form"
                    )

        resolved_machine_roots = {
            row["form_resolution"]["root_subproblem_id"]
            for row in machines
            if row["form_resolution"]["root_subproblem_id"] is not None
        }
        if resolved_machine_roots != root_ids:
            raise CorpusBridgeError(
                "machine construction roots differ from resolved forms"
            )

        dependency_by_key: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        expected_dependency_bindings: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        for route in forest["items"]:
            execution = route.get("mechanics", {}).get("execution")
            if not isinstance(execution, list):
                raise CorpusBridgeError(
                    "construction route omits execution mechanics"
                )
            grouped: dict[str, dict[str, Any]] = {}
            for binding in execution:
                machine = (
                    binding.get("machine")
                    if isinstance(binding, dict)
                    else None
                )
                if (
                    not isinstance(machine, dict)
                    or not isinstance(machine.get("id"), str)
                ):
                    raise CorpusBridgeError(
                        "construction route has an invalid execution binding"
                    )
                machine_id = str(machine["id"])
                value = grouped.setdefault(
                    machine_id,
                    {"machine": machine, "bindings": []},
                )
                if value["machine"] != machine:
                    raise CorpusBridgeError(
                        "construction route execution machine record drifted"
                    )
                value["bindings"].append(binding)
            for machine_id, value in grouped.items():
                expected_dependency_bindings[
                    (
                        route["id"],
                        route["subproblem_id"],
                        machine_id,
                    )
                ] = value
        for dependency in machine_construction[
            "machine_dependency_edges"
        ]:
            route_id = dependency["route_id"]
            subproblem_id = dependency["from_subproblem_id"]
            machine_id = dependency["machine"]["id"]
            key = (route_id, subproblem_id, machine_id)
            if key in dependency_by_key:
                raise CorpusBridgeError(
                    "machine dependency edge is duplicated"
                )
            dependency_by_key[key] = dependency
            route = route_by_id.get(route_id)
            machine_row = machine_by_id.get(machine_id)
            if (
                route is None
                or route["subproblem_id"] != subproblem_id
                or machine_row is None
                or machine_row["machine"] != dependency["machine"]
                or any(
                    binding.get("machine") != dependency["machine"]
                    for binding in dependency["execution_bindings"]
                )
            ):
                raise CorpusBridgeError(
                    "machine dependency edge differs from its route or machine"
                )
            expected_binding = expected_dependency_bindings.get(key)
            if (
                expected_binding is None
                or expected_binding["machine"] != dependency["machine"]
                or expected_binding["bindings"]
                != dependency["execution_bindings"]
            ):
                raise CorpusBridgeError(
                    "machine dependency lacks retained execution evidence"
                )
            resolution = machine_row["form_resolution"]
            expected_target = resolution["root_subproblem_id"]
            expected_status = (
                "resolved"
                if expected_target is not None
                else "max-machine-roots"
                if any(
                    row["reason"] == "max-machine-roots"
                    and row["machine"]["id"] == machine_id
                    for row in machine_construction["unresolved"]
                )
                else resolution["status"] + "-form"
                if resolution["status"] != "resolved"
                else "unseeded-form-root"
            )
            if (
                dependency["to_subproblem_id"] != expected_target
                or dependency["status"] != expected_status
            ):
                raise CorpusBridgeError(
                    "machine dependency resolution differs from machine form"
                )
        if set(dependency_by_key) != set(expected_dependency_bindings):
            raise CorpusBridgeError(
                "machine dependencies differ from construction execution "
                "mechanics"
            )
        if set(dependency_by_key) != set(construction_requirements):
            raise CorpusBridgeError(
                "machine dependency edges differ from construction provenance"
            )
        for key, requirement in construction_requirements.items():
            dependency = dependency_by_key[key]
            if (
                requirement["execution_bindings"]
                != dependency["execution_bindings"]
            ):
                raise CorpusBridgeError(
                    "machine dependency execution bindings drifted"
                )

        unresolved_by_machine: dict[str, str] = {}
        for unresolved in machine_construction["unresolved"]:
            machine_id = unresolved["machine"]["id"]
            if machine_id in unresolved_by_machine:
                raise CorpusBridgeError(
                    "machine construction unresolved state is duplicated"
                )
            machine_row = machine_by_id.get(machine_id)
            if (
                machine_row is None
                or machine_row["machine"] != unresolved["machine"]
            ):
                raise CorpusBridgeError(
                    "machine construction unresolved state names an "
                    "unknown machine"
                )
            resolution = machine_row["form_resolution"]
            reason = unresolved["reason"]
            valid = (
                reason == "unresolved-form"
                and resolution["status"] == "unresolved"
                or reason == "ambiguous-form"
                and resolution["status"] == "ambiguous"
                or reason in {
                    "max-machine-roots",
                    "unseeded-form-root",
                }
                and resolution["status"] == "resolved"
                and resolution["root_subproblem_id"] is None
            )
            if not valid:
                raise CorpusBridgeError(
                    "machine construction unresolved reason differs from "
                    "form resolution"
                )
            unresolved_by_machine[machine_id] = reason
        expected_unresolved_machine_ids = {
            row["machine"]["id"]
            for row in machines
            if row["form_resolution"]["status"] != "resolved"
            or row["form_resolution"]["root_subproblem_id"] is None
        }
        if set(unresolved_by_machine) != expected_unresolved_machine_ids:
            raise CorpusBridgeError(
                "machine construction unresolved coverage differs from "
                "machine forms"
            )

        expected_cycles = _construction_cycles(
            forest,
            machine_construction["machine_dependency_edges"],
        )
        if machine_construction["bootstrapping_cycles"] != expected_cycles:
            raise CorpusBridgeError(
                "machine construction cycles differ from dependency graph"
            )
        expected_reasons = list(forest["truncation"]["reasons"])
        if any(
            row["reason"] == "max-machine-roots"
            for row in machine_construction["unresolved"]
        ):
            expected_reasons.append("max-machine-roots")
        truncation = machine_construction["truncation"]
        if (
            truncation["truncated"] != bool(expected_reasons)
            or truncation["reasons"] != expected_reasons
            or {
                key: value
                for key, value in truncation["limits"].items()
                if key != "max_machine_roots"
            }
            != forest["truncation"]["limits"]
            or len(root_ids)
            > truncation["limits"]["max_machine_roots"]
        ):
            raise CorpusBridgeError(
                "machine construction truncation differs from its forest"
            )

    classification = answer.get("classification")
    if classification is not None:
        observed_counts: dict[str, int] = defaultdict(int)
        for item in classification["items"]:
            row_type = item.get("row_type")
            row = item.get("row")
            if not isinstance(row, dict):
                raise CorpusBridgeError(
                    "classification item omits its exact row"
                )
            observed = item.get("classification")
            if not isinstance(observed, str) or not observed:
                raise CorpusBridgeError(
                    "classification item has no classification"
                )
            observed_counts[observed] += 1
            if row_type == "runtime-owner":
                mechanics = item.get("mechanics")
                occurrences = item.get("occurrences")
                if (
                    not isinstance(mechanics, dict)
                    or observed != mechanics.get("classification")
                    or item.get("eligible") != mechanics.get("eligible")
                    or not isinstance(occurrences, dict)
                ):
                    raise CorpusBridgeError(
                        "runtime row classification differs from mechanics"
                    )
                expected_roles = sorted(
                    role
                    for role in ("producer", "consumer")
                    if occurrences.get(role + "s")
                )
                if item.get("roles") != expected_roles:
                    raise CorpusBridgeError(
                        "runtime row classification roles differ from occurrences"
                    )
                for role in ("producers", "consumers"):
                    for occurrence in occurrences.get(role, []):
                        if occurrence.get("owner") != row:
                            raise CorpusBridgeError(
                                "classification occurrence has a different owner"
                            )
            elif row_type == "presentation":
                scope = _record_scope(row)
                if (
                    observed != "presentation-only"
                    or item.get("eligible") is not False
                    or item.get("roles") != ["presentation"]
                    or scope.profile != "CLIENT_JEI_FINAL_STATE"
                    or scope.physical_side != "CLIENT"
                ):
                    raise CorpusBridgeError(
                        "presentation row classification is invalid"
                    )
                for reference in item.get("presentation_for", []):
                    relationship = reference.get("relationship")
                    runtime_row = reference.get("runtime_row")
                    if (
                        not isinstance(relationship, dict)
                        or not isinstance(runtime_row, dict)
                        or relationship.get("predicate") != "reconciles_to"
                        or relationship.get("subject") != row.get("id")
                        or relationship.get("object") != runtime_row.get("id")
                    ):
                        raise CorpusBridgeError(
                            "presentation classification lacks exact reconciliation"
                        )
            else:
                raise CorpusBridgeError(
                    f"unsupported classification row type: {row_type}"
                )
        expected_counts = [
            {
                "classification": name,
                "count": observed_counts[name],
            }
            for name in sorted(observed_counts)
        ]
        if classification["counts"] != expected_counts:
            raise CorpusBridgeError(
                "classification counts differ from classified rows"
            )

    comparison = answer.get("comparison")
    if comparison is not None:
        if projection_scopes != PROFILE_COMPARISON_SCOPES:
            raise CorpusBridgeError(
                "comparison answer does not cover the four required scopes"
            )
        if comparison["authority_policy"] != COMPARISON_AUTHORITY_POLICY:
            raise CorpusBridgeError(
                "comparison authority policy differs from the contract"
            )
        scope_rows = comparison["scopes"]
        expected_scope_keys = [
            scope.link_value
            for scope in PROFILE_COMPARISON_SCOPES
        ]
        observed_scope_keys = [
            _comparison_scope_key(row["scope"])
            for row in scope_rows
        ]
        if observed_scope_keys != expected_scope_keys:
            raise CorpusBridgeError(
                "comparison scopes are missing, duplicated, or unordered"
            )
        availability_by_scope = {
            f"{row['profile']}/{row['physical_side']}": row["availability"]
            for row in answer["profile_scope"]
        }

        for scope_row in scope_rows:
            scope = Scope(
                scope_row["scope"]["profile"],
                scope_row["scope"]["physical_side"],
            )
            target = scope_row["target"]
            target_nodes = target["nodes"]
            expected_target_status = (
                "missing"
                if not target_nodes
                else "observed"
                if len(target_nodes) == 1
                else "ambiguous"
            )
            expected_target_fingerprints = (
                _comparison_record_fingerprints(target_nodes)
            )
            if (
                target["status"] != expected_target_status
                or target["semantic_fingerprints"]
                != expected_target_fingerprints
                or target["semantic_set_sha256"]
                != _comparison_set_sha256(expected_target_fingerprints)
            ):
                raise CorpusBridgeError(
                    "comparison target state or semantic digest differs"
                )
            expected_availability = (
                "missing"
                if expected_target_status == "missing"
                else "consulted"
            )
            if availability_by_scope[scope.link_value] != expected_availability:
                raise CorpusBridgeError(
                    "comparison target state differs from profile_scope"
                )
            for node in target_nodes:
                if _record_scope(node) != scope:
                    raise CorpusBridgeError(
                        "comparison target escaped its scope"
                    )

            mechanics = scope_row["mechanics"]
            mechanics_items = mechanics["items"]
            expected_mechanics_status = (
                "observed"
                if mechanics_items
                else "none-observed"
                if expected_target_status == "observed"
                else "ambiguous-target"
                if expected_target_status == "ambiguous"
                else "unavailable"
            )
            for item in mechanics_items:
                if (
                    item.get("row_type") != "runtime-owner"
                    or item.get("scope") != scope_row["scope"]
                    or not isinstance(item.get("row"), dict)
                    or not isinstance(item.get("mechanics"), dict)
                    or not isinstance(item.get("occurrences"), dict)
                    or "presentation" in item["mechanics"]
                    or "evidence_ids" in item["mechanics"]
                ):
                    raise CorpusBridgeError(
                        "comparison mechanics item mixes authority or scope"
                    )
                for occurrences in item["occurrences"].values():
                    for occurrence in occurrences:
                        occurrence_mechanics = occurrence.get("mechanics")
                        if (
                            not isinstance(occurrence_mechanics, dict)
                            or "presentation" in occurrence_mechanics
                            or "evidence_ids" in occurrence_mechanics
                        ):
                            raise CorpusBridgeError(
                                "comparison occurrence mixes presentation "
                                "with mechanics"
                            )
                for record in _runtime_records_in(item).values():
                    if _record_scope(record) != scope:
                        raise CorpusBridgeError(
                            "comparison mechanics record escaped its scope"
                        )
            expected_mechanics_fingerprints = (
                _comparison_item_fingerprints(mechanics_items)
            )
            if (
                mechanics["status"] != expected_mechanics_status
                or mechanics["semantic_fingerprints"]
                != expected_mechanics_fingerprints
                or mechanics["semantic_set_sha256"]
                != _comparison_set_sha256(
                    expected_mechanics_fingerprints
                )
            ):
                raise CorpusBridgeError(
                    "comparison mechanics state or semantic digest differs"
                )

            presentation = scope_row["presentation"]
            presentation_items = presentation["items"]
            expected_presentation_status = (
                "observed-via-reconciliation"
                if presentation_items
                else "none-observed"
                if expected_target_status == "observed"
                else "ambiguous-target"
                if expected_target_status == "ambiguous"
                else "unavailable"
            )
            for item in presentation_items:
                row = item.get("row")
                if (
                    item.get("row_type") != "presentation"
                    or item.get("scope") != scope_row["scope"]
                    or not isinstance(row, dict)
                    or _record_scope(row) != scope
                    or scope
                    != Scope("CLIENT_JEI_FINAL_STATE", "CLIENT")
                    or item.get("classification") != "presentation-only"
                    or item.get("eligible") is not False
                    or item.get("roles") != ["presentation"]
                    or not item.get("presentation_for")
                ):
                    raise CorpusBridgeError(
                        "comparison presentation item mixes authority or scope"
                    )
                for reference in item["presentation_for"]:
                    relationship = reference.get("relationship")
                    runtime_row = reference.get("runtime_row")
                    if (
                        not isinstance(relationship, dict)
                        or not isinstance(runtime_row, dict)
                        or relationship.get("predicate") != "reconciles_to"
                        or relationship.get("subject") != row.get("id")
                        or relationship.get("object")
                        != runtime_row.get("id")
                    ):
                        raise CorpusBridgeError(
                            "comparison presentation lacks exact reconciliation"
                        )
            expected_presentation_fingerprints = (
                _comparison_item_fingerprints(presentation_items)
            )
            if (
                presentation["status"] != expected_presentation_status
                or presentation["semantic_fingerprints"]
                != expected_presentation_fingerprints
                or presentation["semantic_set_sha256"]
                != _comparison_set_sha256(
                    expected_presentation_fingerprints
                )
            ):
                raise CorpusBridgeError(
                    "comparison presentation state or semantic digest differs"
                )

            for field in ("target", "mechanics", "presentation"):
                section = scope_row[field]
                expected_ids = sorted(
                    _validated_runtime_projection_records(
                        section,
                        scopes=projection_scopes,
                        snapshot_id=answer["snapshot_id"],
                    )
                )
                if section["evidence_ids"] != expected_ids:
                    raise CorpusBridgeError(
                        f"comparison {field} evidence closure differs"
                    )
                if field == "mechanics" and any(
                    by_id[identifier]["basis"] != "runtime-mechanics"
                    for identifier in expected_ids
                ):
                    raise CorpusBridgeError(
                        "comparison mechanics cites non-mechanical evidence"
                    )
                if (
                    field == "presentation"
                    and presentation_items
                    and "runtime-presentation"
                    not in {
                        by_id[identifier]["basis"]
                        for identifier in expected_ids
                    }
                ):
                    raise CorpusBridgeError(
                        "comparison presentation lacks presentation evidence"
                    )
            expected_scope_ids = sorted(
                set(target["evidence_ids"])
                | set(mechanics["evidence_ids"])
                | set(presentation["evidence_ids"])
            )
            if scope_row["evidence_ids"] != expected_scope_ids:
                raise CorpusBridgeError(
                    "comparison scope evidence closure differs"
                )

        expected_status = (
            "answered-with-missing-targets"
            if any(
                row["target"]["status"] == "observed"
                for row in scope_rows
            )
            and any(
                row["target"]["status"] == "missing"
                for row in scope_rows
            )
            else "answered"
            if any(
                row["target"]["status"] == "observed"
                for row in scope_rows
            )
            else "unresolved"
        )
        if comparison["status"] != expected_status:
            raise CorpusBridgeError(
                "comparison status differs from target availability"
            )
        if comparison["pairwise"] != _comparison_pairwise(scope_rows):
            raise CorpusBridgeError(
                "comparison pairwise deltas differ from scope observations"
            )
        if comparison["summary"] != _comparison_summary(scope_rows):
            raise CorpusBridgeError(
                "comparison summary differs from scope observations"
            )

    confidence = answer.get("confidence")
    if confidence is not None:
        if (
            confidence["snapshot_id"] != answer["snapshot_id"]
            or confidence["profile_scope"] != answer["profile_scope"]
        ):
            raise CorpusBridgeError(
                "confidence scope differs from the answer scope"
            )
        expected_statement = (
            "verified only for the named snapshot and requested profile/side "
            "entries; no cross-version or global validity is claimed"
        )
        if confidence["scope_statement"] != expected_statement:
            raise CorpusBridgeError(
                "confidence overstates its verification scope"
            )
        check_ids = {
            identifier
            for check in confidence["checks"]
            for identifier in check["evidence_ids"]
        }
        if confidence["evidence_ids"] != sorted(check_ids):
            raise CorpusBridgeError(
                "confidence evidence differs from its check closure"
            )
        confidence_ids = confidence["evidence_ids"]
        missing_confidence_ids = sorted(set(confidence_ids) - set(by_id))
        if missing_confidence_ids:
            raise CorpusBridgeError(
                "confidence references missing evidence: "
                + ", ".join(missing_confidence_ids)
            )
        observed_bases = sorted(
            {by_id[identifier]["basis"] for identifier in confidence_ids}
        )
        required_bases = sorted(question["required_evidence_bases"])
        missing_bases = sorted(set(required_bases) - set(observed_bases))
        if confidence["evidence_bases"] != {
            "required": required_bases,
            "observed": observed_bases,
            "missing": missing_bases,
        }:
            raise CorpusBridgeError(
                "confidence evidence-basis assessment is inconsistent"
            )
        expected_gap_codes = sorted(
            {row["code"] for row in answer["known_gaps"]}
        )
        if confidence["known_gap_codes"] != expected_gap_codes:
            raise CorpusBridgeError(
                "confidence gap codes differ from known_gaps"
            )
        checks = {row["name"]: row for row in confidence["checks"]}
        if len(checks) != len(confidence["checks"]) or set(checks) != {
            "exact-identity",
            "snapshot-bound",
            "profile-coverage",
            "required-evidence-bases",
            "quest-guidance-link",
        }:
            raise CorpusBridgeError(
                "confidence checks are missing or duplicated"
            )
        assertions_by_path = {
            row["path"]: row
            for row in answer["assertions"]
        }
        target_ids = assertions_by_path["/target"]["evidence_ids"]
        runtime_ids = assertions_by_path["/runtime"]["evidence_ids"]
        quest_ids = assertions_by_path["/quests"]["evidence_ids"]
        expected_exact_status = (
            "passed"
            if answer["target"]["resolution_status"] == "resolved"
            else "failed"
        )
        missing_profiles = [
            f"{row['profile']}/{row['physical_side']}"
            for row in answer["profile_scope"]
            if row["availability"] == "missing"
        ]
        expected_checks = {
            "exact-identity": {
                "status": expected_exact_status,
                "detail": (
                    "canonical and runtime identities resolve exactly"
                    if expected_exact_status == "passed"
                    else (
                        "canonical or runtime identity is unresolved or "
                        "ambiguous"
                    )
                ),
                "evidence_ids": target_ids,
            },
            "snapshot-bound": {
                "status": "passed" if confidence_ids else "failed",
                "detail": (
                    "all cited records are bound to "
                    + answer["snapshot_id"]
                    if confidence_ids
                    else (
                        "no evidence records are available to bind the "
                        "snapshot"
                    )
                ),
                "evidence_ids": confidence_ids,
            },
            "profile-coverage": {
                "status": "partial" if missing_profiles else "passed",
                "detail": (
                    "missing exact runtime target in: "
                    + ", ".join(missing_profiles)
                    if missing_profiles
                    else "every requested profile/side was consulted"
                ),
                "evidence_ids": runtime_ids,
            },
            "required-evidence-bases": {
                "status": "failed" if missing_bases else "passed",
                "detail": (
                    "missing required evidence bases: "
                    + ", ".join(missing_bases)
                    if missing_bases
                    else "all question-required evidence bases are present"
                ),
                "evidence_ids": sorted(
                    identifier
                    for identifier, row in by_id.items()
                    if row["basis"] in required_bases
                ),
            },
            "quest-guidance-link": {
                "status": "passed" if quest_ids else "not-evaluated",
                "detail": (
                    "exact quest guidance is linked to the runtime identity"
                    if quest_ids
                    else "no exact quest guidance link is available"
                ),
                "evidence_ids": quest_ids,
            },
        }
        for name, expected in expected_checks.items():
            if checks[name] != {"name": name, **expected}:
                raise CorpusBridgeError(
                    f"confidence check differs from answer state: {name}"
                )
        if (
            checks["exact-identity"]["status"] != "passed"
            or missing_bases
            or not confidence_ids
        ):
            expected_confidence = "unresolved"
        elif answer["known_gaps"]:
            expected_confidence = "verified-with-known-gaps"
        else:
            expected_confidence = "verified"
        if confidence["status"] != expected_confidence:
            raise CorpusBridgeError(
                "confidence status differs from its verification checks"
            )
        if confidence["boundaries"] != {
            "global_validity_claimed": False,
            "profile_comparison_evaluated": False,
            "mechanical_route_evaluated": False,
            "quest_prerequisites_are_mechanical": False,
        }:
            raise CorpusBridgeError(
                "confidence boundaries are not fail-closed"
            )

    pinned_source = answer["pinned_source"]
    if pinned_source["status"] == "answered":
        require_projected_record(
            pinned_source["entity"],
            authority="curated-catalog",
            record_kind="curated-entity",
            context="pinned_source.entity",
        )
        for field, record_kind in (
            ("claims", "curated-claim"),
            ("relations", "curated-relation"),
            ("sources", "curated-entity"),
        ):
            for index, record in enumerate(pinned_source[field]):
                require_projected_record(
                    record,
                    authority="curated-catalog",
                    record_kind=record_kind,
                    context=f"pinned_source.{field}[{index}]",
                )
        infrastructure_authorities = pinned_source.get(
            "infrastructure_authorities"
        )
        if infrastructure_authorities is not None:
            if not isinstance(infrastructure_requirements, dict):
                raise CorpusBridgeError(
                    "pinned infrastructure authority has no infrastructure "
                    "projection"
                )
            expected = _infrastructure_authority_projection(
                infrastructure_requirements
            )
            if infrastructure_authorities != expected:
                raise CorpusBridgeError(
                    "pinned infrastructure authorities differ from the "
                    "semantic projection"
                )
            for field, record_kind in (
                ("authority_sets", "source-authority-set"),
                ("citations", "source-citation"),
            ):
                for index, record in enumerate(
                    infrastructure_authorities[field]
                ):
                    require_projected_record(
                        record,
                        authority="source-authority-registry",
                        record_kind=record_kind,
                        context=(
                            "pinned_source.infrastructure_authorities."
                            f"{field}[{index}]"
                        ),
                    )

    for section_name in ("declaration", "ownership"):
        for index, item in enumerate(answer[section_name]["items"]):
            if not isinstance(item, dict):
                raise CorpusBridgeError(
                    f"{section_name}.items[{index}] is not an object"
                )
            for field, record_kind in (
                ("relationship", "curated-relation"),
                ("source", "curated-entity"),
            ):
                require_projected_record(
                    item.get(field),
                    authority="curated-catalog",
                    record_kind=record_kind,
                    context=f"{section_name}.items[{index}].{field}",
                )
            claim = item.get("claim")
            if claim is not None:
                require_projected_record(
                    claim,
                    authority="curated-catalog",
                    record_kind="curated-claim",
                    context=f"{section_name}.items[{index}].claim",
                )

    for quest_index, quest in enumerate(answer["quests"]):
        if not isinstance(quest, dict):
            raise CorpusBridgeError(f"quests[{quest_index}] is not an object")
        require_projected_record(
            quest.get("quest"),
            authority="quest-graph",
            record_kind="quest-node",
            context=f"quests[{quest_index}].quest",
        )
        for match_index, match in enumerate(quest.get("matches", [])):
            if not isinstance(match, dict):
                raise CorpusBridgeError(
                    f"quests[{quest_index}].matches[{match_index}] is not an object"
                )
            require_projected_record(
                match.get("record"),
                authority="quest-graph",
                record_kind="quest-node",
                context=f"quests[{quest_index}].matches[{match_index}].record",
            )
            relationship = match.get("relationship")
            if relationship is not None:
                require_projected_record(
                    relationship,
                    authority="quest-graph",
                    record_kind="quest-edge",
                    context=(
                        f"quests[{quest_index}].matches[{match_index}].relationship"
                    ),
                )
        for line_index, membership in enumerate(quest.get("quest_lines", [])):
            if not isinstance(membership, dict):
                raise CorpusBridgeError(
                    f"quests[{quest_index}].quest_lines[{line_index}] is not an object"
                )
            for field, record_kind in (
                ("relationship", "quest-edge"),
                ("quest_line", "quest-node"),
            ):
                require_projected_record(
                    membership.get(field),
                    authority="quest-graph",
                    record_kind=record_kind,
                    context=(
                        f"quests[{quest_index}].quest_lines[{line_index}].{field}"
                    ),
                )

    for index, prerequisite in enumerate(answer["quest_prerequisites"]):
        if not isinstance(prerequisite, dict):
            raise CorpusBridgeError(
                f"quest_prerequisites[{index}] is not an object"
            )
        for field, record_kind in (
            ("quest", "quest-node"),
            ("dependent_quest", "quest-node"),
            ("relationship", "quest-edge"),
            ("prerequisite", "quest-node"),
        ):
            require_projected_record(
                prerequisite.get(field),
                authority="quest-graph",
                record_kind=record_kind,
                context=f"quest_prerequisites[{index}].{field}",
            )

    if answer["result_status"] in {"answered", "answered-with-profile-gap"}:
        observed_bases = {by_id[identifier]["basis"] for identifier in referenced}
        required_bases = set(question["required_evidence_bases"])
        if not required_bases.issubset(observed_bases):
            raise CorpusBridgeError(
                "answered result lacks required evidence bases: "
                f"{sorted(required_bases - observed_bases)}"
            )


def build_answer(
    *,
    runtime_database: Path,
    catalog_root: Path | None,
    question_path: Path,
    question_id: str,
    links_path: Path | None,
    quest_nodes_path: Path | None,
    quest_edges_path: Path | None,
    snapshot_id: str,
    scope_values: Iterable[str],
    chain_options: ChainOptions = ChainOptions(),
    recycling_options: RecyclingOptions = RecyclingOptions(),
    construction_chain_options: ChainOptions = (
        DEFAULT_CONSTRUCTION_CHAIN_OPTIONS
    ),
    max_construction_machine_roots: int = (
        DEFAULT_MAX_CONSTRUCTION_MACHINE_ROOTS
    ),
    validated_query: atlas_query.ValidatedQuery | None = None,
    expected_resolution: atlas_query.QueryResolution | None = None,
    optional_authorities: bool = False,
) -> dict[str, Any]:
    """Build one deterministic exact-selector answer."""

    question = _load_question(question_path, question_id)
    scopes = _parse_scopes(scope_values)
    if validated_query is not None:
        if (
            validated_query.question != question
            or validated_query.instance["question_id"] != question_id
            or validated_query.instance["snapshot_id"] != snapshot_id
            or [
                {
                    "profile": scope.profile,
                    "physical_side": scope.physical_side,
                }
                for scope in scopes
            ]
            != validated_query.instance["scopes"]
        ):
            raise CorpusBridgeError(
                "parameterized query identity or scope differs from bridge inputs"
            )
    if (
        "comparison" in question["required_fields"]
        and scopes != PROFILE_COMPARISON_SCOPES
    ):
        raise CorpusBridgeError(
            "profile comparison requires exactly COMMON client, COMMON "
            "dedicated, CLIENT_JEI client, and OFFLINE scopes"
        )
    if validated_query is None:
        selector = question["selector"]
        key_kind = PRIMARY_RUNTIME_KEYS.get(selector["kind"])
        if key_kind is None:
            raise CorpusBridgeError(
                "question selector kind has no unambiguous primary runtime key in "
                f"the bounded bridge: {selector['kind']}"
            )
        runtime_kind = selector["kind"].replace("-", "_")
        if runtime_kind == "ore_dictionary":
            runtime_kind = "ore_dictionary_key"
    else:
        query_selector = validated_query.instance["selector"]
        selector = {
            "kind": query_selector["kind"],
            "key": query_selector["key"],
        }
        key_kind = query_selector["key_kind"]
        if validated_query.selector_policy is None:
            raise CorpusBridgeError(
                "parameterized bridge cannot compose an unsupported target kind"
            )
        runtime_kind = validated_query.selector_policy["runtime_kind"]

    catalogs = _optional_catalogs(
        catalog_root,
        optional=optional_authorities,
    )
    entities = catalogs["entities.jsonl"]
    entities_by_id = {row["id"]: row for row in entities}
    links = _optional_links(
        links_path,
        snapshot_id,
        entities_by_id,
        optional=optional_authorities,
    )
    quest_nodes, quest_edges = _optional_quest_graph(
        quest_nodes_path,
        quest_edges_path,
        snapshot_id,
        optional=optional_authorities,
    )

    with RuntimeGraphReader(runtime_database) as reader:
        runtime_nodes, profile_gaps, runtime_ambiguous = _resolve_runtime(
            reader,
            key_kind,
            selector["key"],
            runtime_kind,
            scopes,
            snapshot_id,
        )
    if expected_resolution is not None:
        observed_targets = sorted(
            (
                row["id"],
                row["kind"],
                _record_scope(row).profile,
                _record_scope(row).physical_side,
            )
            for row in runtime_nodes
        )
        expected_targets = sorted(
            (
                target.node["id"],
                target.node["kind"],
                target.scope.profile,
                target.scope.physical_side,
            )
            for target in expected_resolution.targets
        )
        observed_gaps = sorted(
            (scope.profile, scope.physical_side)
            for scope in profile_gaps
        )
        expected_gaps = sorted(
            (scope.profile, scope.physical_side)
            for scope in expected_resolution.profile_gaps
        )
        expected_ambiguous = expected_resolution.status == "ambiguous"
        if (
            observed_targets != expected_targets
            or observed_gaps != expected_gaps
            or runtime_ambiguous != expected_ambiguous
        ):
            raise CorpusBridgeError(
                "runtime target resolution changed between parameterized "
                "preflight and answer composition"
            )

    catalog_candidates = _exact_catalog_candidates(
        entities,
        selector["kind"],
        selector["key"],
        snapshot_id,
    )
    runtime_ids = {row["id"] for row in runtime_nodes}
    requested_scope_values = {scope.link_value for scope in scopes}
    runtime_links = [
        row
        for row in links
        if row["observed_kind"] == "runtime-node"
        and row["key_kind"] == key_kind
        and row["key_value"] == selector["key"]
        and row["observed_id"] in runtime_ids
        and requested_scope_values.intersection(row["profile_scope"])
    ]
    runtime_links.sort(key=lambda row: row["id"])
    unlinked_runtime_ids: list[str] = []
    runtime_link_ambiguous = False
    for node in runtime_nodes:
        scope = _record_scope(node)
        matching = [
            row
            for row in runtime_links
            if row["observed_id"] == node["id"]
            and scope.link_value in row["profile_scope"]
        ]
        if not matching:
            unlinked_runtime_ids.append(str(node["id"]))
        elif len(matching) > 1:
            runtime_link_ambiguous = True

    linked_canonical_ids = {
        row["canonical_entity_id"] for row in runtime_links
    }
    candidate_ids = {row["id"] for row in catalog_candidates}
    bound_ids = sorted(linked_canonical_ids.intersection(candidate_ids))
    canonical = (
        next(row for row in catalog_candidates if row["id"] == bound_ids[0])
        if len(bound_ids) == 1
        else None
    )
    catalog_ambiguous = (
        len(catalog_candidates) > 1
        and len(bound_ids) != 1
    )
    authority_ambiguous = (
        runtime_link_ambiguous
        or catalog_ambiguous
        or len(linked_canonical_ids) > 1
        or len(bound_ids) > 1
    )
    if validated_query is None:
        ambiguous = runtime_ambiguous or authority_ambiguous
        resolved = (
            bool(runtime_nodes)
            and not unlinked_runtime_ids
            and canonical is not None
            and not ambiguous
        )
    else:
        ambiguous = runtime_ambiguous
        resolved = bool(runtime_nodes) and not ambiguous

    evidence: dict[str, dict[str, Any]] = {}
    runtime_evidence_ids: list[str] = []
    for node in runtime_nodes:
        runtime_evidence_ids.append(
            _add_runtime_evidence(evidence, node)
        )

    required_fields = set(question["required_fields"])
    needs_infrastructure = "infrastructure_requirements" in required_fields
    infrastructure_authority_registry = (
        _load_infrastructure_authority_registry(snapshot_id)
        if needs_infrastructure
        else None
    )
    needs_recipes = bool(
        {"recipes", "executable_machines"} & required_fields
    )
    needs_comparison = "comparison" in required_fields
    needs_classification = (
        "classification" in required_fields
        or needs_comparison
    )
    producers: dict[str, Any] | None = None
    consumers: dict[str, Any] | None = None
    routes: dict[str, Any] | None = None
    recipes: dict[str, Any] | None = None
    executable_machines: dict[str, Any] | None = None
    ingredient_slots: dict[str, Any] | None = None
    reusable_requirements: dict[str, Any] | None = None
    byproducts: dict[str, Any] | None = None
    recycling_paths: dict[str, Any] | None = None
    route_prerequisites: dict[str, Any] | None = None
    machine_construction: dict[str, Any] | None = None
    infrastructure_requirements: dict[str, Any] | None = None
    classification: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    with RuntimeGraphReader(runtime_database) as reader:
        if (
            "producers" in required_fields
            or needs_recipes
            or needs_classification
        ):
            producers = _usage_projection(
                reader,
                direction="producers",
                key_kind=key_kind,
                key_value=selector["key"],
                runtime_kind=runtime_kind,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if (
            "consumers" in required_fields
            or needs_recipes
            or needs_classification
        ):
            consumers = _usage_projection(
                reader,
                direction="consumers",
                key_kind=key_kind,
                key_value=selector["key"],
                runtime_kind=runtime_kind,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if needs_recipes:
            if producers is None or consumers is None:
                raise CorpusBridgeError(
                    "recipe projection requires producer and consumer occurrences"
                )
            recipes = _recipe_projection(
                reader,
                producers=producers,
                consumers=consumers,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if "executable_machines" in required_fields:
            if recipes is None:
                raise CorpusBridgeError(
                    "executable-machine projection requires recipes"
                )
            executable_machines = _executable_machine_projection(
                recipes,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if needs_classification:
            if producers is None or consumers is None:
                raise CorpusBridgeError(
                    "classification projection requires usage occurrences"
                )
            classification = _classification_projection(
                reader,
                selector=NodeSelector.by_key(
                    key_kind,
                    selector["key"],
                    kind=runtime_kind,
                ),
                producers=producers,
                consumers=consumers,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
            if needs_comparison:
                comparison = _comparison_projection(
                    classification=classification,
                    runtime_nodes=runtime_nodes,
                    scopes=scopes,
                    snapshot_id=snapshot_id,
                    evidence=evidence,
                )
        if (
            "routes" in required_fields
            or "byproducts" in required_fields
            or "recycling_paths" in required_fields
            or needs_infrastructure
        ):
            routes = _route_projection(
                reader,
                key_kind=key_kind,
                key_value=selector["key"],
                runtime_kind=runtime_kind,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
                options=chain_options,
            )
        if "ingredient_slots" in required_fields:
            if routes is None:
                raise CorpusBridgeError(
                    "ingredient-slot projection requires routes"
                )
            ingredient_slots = _route_detail_projection(
                routes,
                field="ingredient_slots",
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if "reusable_requirements" in required_fields:
            if routes is None:
                raise CorpusBridgeError(
                    "reusable-requirement projection requires routes"
                )
            reusable_requirements = _route_detail_projection(
                routes,
                field="reusable_requirements",
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if (
            "byproducts" in required_fields
            or "recycling_paths" in required_fields
        ):
            if routes is None:
                raise CorpusBridgeError(
                    "byproduct projection requires routes"
                )
            byproducts = _byproduct_projection(
                routes,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if "recycling_paths" in required_fields:
            if routes is None or byproducts is None:
                raise CorpusBridgeError(
                    "recycling projection requires routes and byproducts"
                )
            recycling_paths = _recycling_projection(
                reader,
                routes=routes,
                byproducts=byproducts,
                options=recycling_options,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if "prerequisites" in required_fields or needs_infrastructure:
            if routes is None:
                raise CorpusBridgeError(
                    "prerequisite projection requires routes"
                )
            route_prerequisites = _prerequisite_projection(
                routes,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )
        if "machine_construction" in required_fields or needs_infrastructure:
            if route_prerequisites is None:
                raise CorpusBridgeError(
                    "machine-construction projection requires prerequisites"
                )
            machine_construction = _machine_construction_projection(
                reader,
                route_prerequisites,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=(
                    evidence
                    if "machine_construction" in required_fields
                    else {}
                ),
                options=construction_chain_options,
                max_machine_roots=max_construction_machine_roots,
            )
        if needs_infrastructure:
            if (
                routes is None
                or machine_construction is None
                or infrastructure_authority_registry is None
            ):
                raise CorpusBridgeError(
                    "infrastructure projection requires routes, machine "
                    "construction, and source authority"
                )
            infrastructure_requirements = _infrastructure_projection(
                reader,
                routes,
                machine_construction,
                authority_registry=infrastructure_authority_registry,
                scopes=scopes,
                snapshot_id=snapshot_id,
                evidence=evidence,
            )

    pinned_source, declaration, ownership, curated_ids = _curated_projection(
        canonical,
        catalogs,
        evidence,
    )
    if (
        infrastructure_requirements is not None
        and pinned_source["status"] == "answered"
    ):
        infrastructure_authorities = _infrastructure_authority_projection(
            infrastructure_requirements
        )
        pinned_source["infrastructure_authorities"] = (
            infrastructure_authorities
        )
        pinned_source["evidence_ids"] = sorted(
            set(pinned_source["evidence_ids"])
            | set(infrastructure_authorities["evidence_ids"])
        )
    if canonical is None and authority_ambiguous:
        candidate_evidence_ids = set(curated_ids)
        claims_by_id = {
            row["id"]: row for row in catalogs["claims.jsonl"]
        }
        for candidate in catalog_candidates:
            entry = _evidence(
                candidate,
                basis=_curated_basis(candidate, claims_by_id),
                authority="curated-catalog",
                record_kind="curated-entity",
            )
            _add_evidence(evidence, entry)
            candidate_evidence_ids.add(str(entry["id"]))
        curated_ids = tuple(sorted(candidate_evidence_ids))
    with RuntimeGraphReader(runtime_database) as reader:
        quests, prerequisites, quest_ids, quest_link_ids = _quest_projection(
            reader,
            canonical["id"] if resolved and canonical is not None else None,
            runtime_nodes,
            links,
            quest_nodes,
            quest_edges,
            scopes,
            snapshot_id,
            evidence,
        )

    known_gaps: list[dict[str, str]] = []
    for scope in profile_gaps:
        known_gaps.append(
            {
                "code": "profile-gap",
                "detail": f"{scope.profile}/{scope.physical_side} has no exact runtime target",
            }
        )
    if not catalog_candidates:
        known_gaps.append(
            {
                "code": "canonical-identity-missing",
                "detail": "the curated catalog has no exact id or declared alias for the selector",
            }
        )
    elif unlinked_runtime_ids or not bound_ids:
        known_gaps.append(
            {
                "code": "corpus-link-missing",
                "detail": (
                    "one or more resolved runtime targets have no exact corpus "
                    "link to the curated entity: "
                    + ", ".join(sorted(unlinked_runtime_ids))
                    if unlinked_runtime_ids
                    else "no exact corpus link binds the runtime target to the curated entity"
                ),
            }
        )
    if ownership["status"] == "unresolved":
        known_gaps.append(
            {
                "code": "ownership-unresolved",
                "detail": "no curated owns relation directly targets the canonical entity",
            }
        )
    if not quests:
        known_gaps.append(
            {
                "code": "quest-link-missing",
                "detail": "no exact quest record is linked to the canonical entity in the requested scopes",
            }
        )
    for section_name, section in (
        ("producers", producers),
        ("consumers", consumers),
    ):
        if section is not None and section["page"]["truncated"]:
            known_gaps.append(
                {
                    "code": f"{section_name}-page-truncated",
                    "detail": (
                        f"{section_name} returned {section['page']['returned']} "
                        f"of {section['page']['total']} exact occurrences"
                    ),
                }
            )
    if routes is not None and routes["truncation"]["truncated"]:
        known_gaps.append(
            {
                "code": "route-traversal-truncated",
                "detail": (
                    "bounded route traversal reached: "
                    + ", ".join(routes["truncation"]["reasons"])
                ),
            }
        )
    if (
        recycling_paths is not None
        and recycling_paths["truncation"]["truncated"]
    ):
        known_gaps.append(
            {
                "code": "recycling-traversal-truncated",
                "detail": (
                    "bounded recycling traversal reached: "
                    + ", ".join(
                        recycling_paths["truncation"]["reasons"]
                    )
                ),
            }
        )
    if recycling_paths is not None and recycling_paths["unresolved"]:
        known_gaps.append(
            {
                "code": "recycling-paths-unresolved",
                "detail": (
                    "one or more forward uses remain unresolved: "
                    + ", ".join(
                        sorted(
                            {
                                row["reason"]
                                for row in recycling_paths["unresolved"]
                            }
                        )
                    )
                ),
            }
        )
    if (
        machine_construction is not None
        and machine_construction["truncation"]["truncated"]
    ):
        known_gaps.append(
            {
                "code": "machine-construction-truncated",
                "detail": (
                    "bounded machine construction reached: "
                    + ", ".join(
                        machine_construction["truncation"]["reasons"]
                    )
                ),
            }
        )
    if machine_construction is not None:
        unresolved_reasons = sorted(
            {
                row["reason"]
                for row in machine_construction["unresolved"]
            }
        )
        if unresolved_reasons:
            known_gaps.append(
                {
                    "code": "machine-construction-unresolved",
                    "detail": (
                        "one or more machine roots remain unresolved: "
                        + ", ".join(unresolved_reasons)
                    ),
                }
            )
    if (
        infrastructure_requirements is not None
        and infrastructure_requirements["status"] == "partial"
    ):
        known_gaps.append(
            {
                "code": "infrastructure-requirements-partial",
                "detail": (
                    "one or more retained condition candidates remain "
                    "unresolved, source-unbound, or traversal-bounded"
                ),
            }
        )
    missing_required_bases = sorted(
        set(question["required_evidence_bases"])
        - {row["basis"] for row in evidence.values()}
    )
    if missing_required_bases:
        known_gaps.append(
            {
                "code": "required-evidence-missing",
                "detail": (
                    "the acceptance question requires evidence bases that are "
                    "not present: "
                    + ", ".join(missing_required_bases)
                ),
            }
        )

    if ambiguous:
        result_status = "ambiguous"
        resolution_status = "ambiguous"
    elif not resolved:
        result_status = "unresolved-identity"
        resolution_status = "unresolved"
    elif missing_required_bases:
        result_status = "unresolved-evidence"
        resolution_status = "resolved"
    elif routes is not None and routes["status"] == "no-mechanical-route":
        result_status = "no-mechanical-route"
        resolution_status = "resolved"
    elif profile_gaps:
        result_status = "answered-with-profile-gap"
        resolution_status = "resolved"
    else:
        result_status = "answered"
        resolution_status = "resolved"

    reported_canonical_ids = (
        bound_ids
        if len(bound_ids) == 1
        else (
            sorted(candidate_ids)
            if (
                authority_ambiguous
                or (validated_query is None and ambiguous)
            )
            else []
        )
    )
    profile_scope_rows = [
        scope.document(
            "missing" if scope in profile_gaps else "consulted"
        )
        for scope in scopes
    ]
    confidence: dict[str, Any] | None = None
    if "confidence" in required_fields:
        confidence = _confidence_projection(
            question=question,
            resolved=resolved,
            ambiguous=ambiguous,
            runtime_evidence_ids=runtime_evidence_ids,
            curated_evidence_ids=curated_ids,
            quest_evidence_ids=quest_ids,
            profile_scope=profile_scope_rows,
            snapshot_id=snapshot_id,
            known_gaps=known_gaps,
            missing_required_bases=missing_required_bases,
            evidence=evidence,
        )

    section_evidence: dict[str, tuple[str, ...]] = {
        "/target": tuple(sorted(set(runtime_evidence_ids) | set(curated_ids))),
        "/runtime": tuple(sorted(runtime_evidence_ids)),
        "/pinned_source": tuple(pinned_source["evidence_ids"]),
        "/declaration": tuple(declaration["evidence_ids"]),
        "/ownership": tuple(ownership["evidence_ids"]),
        "/quests": quest_ids,
        "/quest_prerequisites": tuple(
            sorted(
                set(quest_ids)
                | {
                    identifier
                    for row in prerequisites
                    for identifier in row["evidence_ids"]
                }
            )
        ),
    }
    if "producers" in required_fields and producers is not None:
        section_evidence["/producers"] = tuple(producers["evidence_ids"])
    if "consumers" in required_fields and consumers is not None:
        section_evidence["/consumers"] = tuple(consumers["evidence_ids"])
    if routes is not None:
        section_evidence["/routes"] = tuple(routes["evidence_ids"])
    if recipes is not None:
        section_evidence["/recipes"] = tuple(recipes["evidence_ids"])
    if executable_machines is not None:
        section_evidence["/executable_machines"] = tuple(
            executable_machines["evidence_ids"]
        )
    if ingredient_slots is not None:
        section_evidence["/ingredient_slots"] = tuple(
            ingredient_slots["evidence_ids"]
        )
    if reusable_requirements is not None:
        section_evidence["/reusable_requirements"] = tuple(
            reusable_requirements["evidence_ids"]
        )
    if byproducts is not None:
        section_evidence["/byproducts"] = tuple(
            byproducts["evidence_ids"]
        )
    if recycling_paths is not None:
        section_evidence["/recycling_paths"] = tuple(
            recycling_paths["evidence_ids"]
        )
    if route_prerequisites is not None:
        section_evidence["/prerequisites"] = tuple(
            route_prerequisites["evidence_ids"]
        )
    if (
        "machine_construction" in required_fields
        and machine_construction is not None
    ):
        section_evidence["/machine_construction"] = tuple(
            machine_construction["evidence_ids"]
        )
    if infrastructure_requirements is not None:
        section_evidence["/infrastructure_requirements"] = tuple(
            infrastructure_requirements["evidence_ids"]
        )
    if (
        "classification" in required_fields
        and classification is not None
    ):
        section_evidence["/classification"] = tuple(
            classification["evidence_ids"]
        )
    if comparison is not None:
        section_evidence["/comparison"] = tuple(
            comparison["evidence_ids"]
        )
    if confidence is not None:
        section_evidence["/confidence"] = tuple(
            confidence["evidence_ids"]
        )
    assertions = [
        {
            "path": path,
            "status": "supported" if identifiers else "unresolved",
            "evidence_ids": list(identifiers),
        }
        for path, identifiers in section_evidence.items()
    ]
    answer = {
        "schema_version": ANSWER_SCHEMA_VERSION,
        "format": ANSWER_FORMAT,
        "question_id": question["id"],
        "query": question["query"],
        "result_status": result_status,
        "snapshot_id": snapshot_id,
        "profile_scope": profile_scope_rows,
        "target": {
            "selector": {
                "kind": selector["kind"],
                "key": selector["key"],
                "key_kind": key_kind,
            },
            "resolution_status": resolution_status,
            "canonical_entity_ids": reported_canonical_ids,
            "runtime_node_ids": sorted(runtime_ids),
            "quest_record_ids": sorted(
                row["observed_id"]
                for row in links
                if row["id"] in set(quest_link_ids)
            ),
            "link_ids": sorted(
                [row["id"] for row in runtime_links] + list(quest_link_ids)
            ),
        },
        "assertions": assertions,
        "evidence": [evidence[identifier] for identifier in sorted(evidence)],
        "runtime": {
            "query": "exact-typed-node",
            "key_kind": key_kind,
            "resolved": list(runtime_nodes),
            "profile_gaps": [
                {
                    "profile": scope.profile,
                    "physical_side": scope.physical_side,
                }
                for scope in profile_gaps
            ],
        },
        "pinned_source": pinned_source,
        "declaration": declaration,
        "ownership": ownership,
        "quests": quests,
        "quest_prerequisites": prerequisites,
        "known_gaps": sorted(
            known_gaps,
            key=lambda row: (row["code"], row["detail"]),
        ),
    }
    if "producers" in required_fields and producers is not None:
        answer["producers"] = producers
    if "consumers" in required_fields and consumers is not None:
        answer["consumers"] = consumers
    if routes is not None:
        answer["routes"] = routes
    if recipes is not None:
        answer["recipes"] = recipes
    if executable_machines is not None:
        answer["executable_machines"] = executable_machines
    if ingredient_slots is not None:
        answer["ingredient_slots"] = ingredient_slots
    if reusable_requirements is not None:
        answer["reusable_requirements"] = reusable_requirements
    if byproducts is not None:
        answer["byproducts"] = byproducts
    if recycling_paths is not None:
        answer["recycling_paths"] = recycling_paths
    if route_prerequisites is not None:
        answer["prerequisites"] = route_prerequisites
    if (
        "machine_construction" in required_fields
        and machine_construction is not None
    ):
        answer["machine_construction"] = machine_construction
    if infrastructure_requirements is not None:
        answer["infrastructure_requirements"] = (
            infrastructure_requirements
        )
    if (
        "classification" in required_fields
        and classification is not None
    ):
        answer["classification"] = classification
    if comparison is not None:
        answer["comparison"] = comparison
    if confidence is not None:
        answer["confidence"] = confidence
    validate_answer(answer, question)
    return answer


def _query_chain_options(
    instance: dict[str, Any],
) -> tuple[ChainOptions, RecyclingOptions, ChainOptions, int]:
    traversal = instance["traversal_options"]
    route = traversal["route"]
    recycling = traversal["recycling"]
    construction = traversal["construction"]
    return (
        ChainOptions(
            max_depth=route["max_depth"],
            max_routes=route["max_routes"],
            max_alternatives_per_slot=(
                route["max_alternatives_per_slot"]
            ),
            include_chanced_outputs=route["include_chanced_outputs"],
            include_procedural_rules=route["include_procedural_rules"],
            max_visited_nodes=route["max_visited_nodes"],
        ),
        RecyclingOptions(
            max_depth=recycling["max_depth"],
            max_operations=recycling["max_operations"],
            max_consumers_per_target=(
                recycling["max_consumers_per_target"]
            ),
            max_output_alternatives_per_slot=(
                recycling["max_output_alternatives_per_slot"]
            ),
            max_visited_targets=recycling["max_visited_targets"],
        ),
        ChainOptions(
            max_depth=construction["max_depth"],
            max_routes=construction["max_routes"],
            max_alternatives_per_slot=(
                construction["max_alternatives_per_slot"]
            ),
            include_chanced_outputs=route["include_chanced_outputs"],
            include_procedural_rules=route["include_procedural_rules"],
            max_visited_nodes=construction["max_visited_nodes"],
        ),
        int(construction["max_machine_roots"]),
    )


def _authority_observations_from_answer(
    answer: dict[str, Any],
    *,
    catalog_available: bool,
    links_available: bool,
    quest_available: bool,
) -> dict[str, atlas_query.AuthorityObservation]:
    evidence_by_basis: dict[str, list[str]] = defaultdict(list)
    evidence_records_by_basis: dict[str, list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for row in answer["evidence"]:
        evidence_by_basis[row["basis"]].append(str(row["id"]))
        evidence_records_by_basis[row["basis"]].append(row)
    for rows in evidence_records_by_basis.values():
        rows.sort(key=lambda row: row["id"])

    observations: dict[str, atlas_query.AuthorityObservation] = {}
    canonical_ambiguous = (
        len(answer["target"]["canonical_entity_ids"]) > 1
    )
    for basis in ("pinned-source", "curated-interpretation"):
        identifiers = tuple(sorted(set(evidence_by_basis[basis])))
        surface_available = catalog_available and links_available
        if identifiers:
            status = "ambiguous" if canonical_ambiguous else "available"
            detail = (
                "multiple exact canonical authority candidates remain"
                if canonical_ambiguous
                else "exact catalog and corpus-link authority is available"
            )
        elif surface_available:
            status = "not-observed"
            detail = (
                "the catalog and corpus-link authority were inspected without "
                "an exact target closure"
            )
        else:
            continue
        observations[basis] = atlas_query.AuthorityObservation(
            basis,
            status,
            identifiers,
            detail,
            tuple(evidence_records_by_basis[basis]),
        )

    quest_ids = tuple(sorted(set(evidence_by_basis["quest-data"])))
    if quest_ids:
        observations["quest-data"] = atlas_query.AuthorityObservation(
            "quest-data",
            "available",
            quest_ids,
            "exact quest records close to the resolved runtime target",
            tuple(evidence_records_by_basis["quest-data"]),
        )
    elif quest_available and links_available and catalog_available:
        observations["quest-data"] = atlas_query.AuthorityObservation(
            "quest-data",
            "not-observed",
            (),
            (
                "the quest graph and corpus links were inspected without an "
                "exact target closure"
            ),
        )
    return observations


def build_query_answer(
    *,
    runtime_database: Path,
    query_instance: dict[str, Any],
    catalog_root: Path | None = DEFAULT_CATALOG_ROOT,
    question_path: Path = DEFAULT_QUESTIONS,
    links_path: Path | None = DEFAULT_PRODUCTION_ROOT / "links.jsonl",
    quest_nodes_path: Path | None = (
        DEFAULT_PRODUCTION_ROOT / "quest-nodes.jsonl"
    ),
    quest_edges_path: Path | None = (
        DEFAULT_PRODUCTION_ROOT / "quest-edges.jsonl"
    ),
    policy_path: Path = atlas_query.DEFAULT_CAPABILITY_POLICY,
) -> dict[str, Any]:
    """Compose one explicit parameterized Atlas query or fail closed."""

    validated = atlas_query.validate_query_instance(
        query_instance,
        question_path=question_path,
        policy_path=policy_path,
    )
    with RuntimeGraphReader(runtime_database) as reader:
        resolution = atlas_query.resolve_validated_query(
            reader,
            validated,
        )
    initial = atlas_query.build_query_preflight(
        validated,
        resolution,
        question_path=question_path,
        policy_path=policy_path,
    )
    if initial["result_status"] in {
        "unsupported-query",
        "ambiguous",
        "unresolved-identity",
        "not-applicable",
    }:
        return initial

    chain_options, recycling_options, construction_options, max_roots = (
        _query_chain_options(query_instance)
    )
    answer = build_answer(
        runtime_database=runtime_database,
        catalog_root=catalog_root,
        question_path=question_path,
        question_id=query_instance["question_id"],
        links_path=links_path,
        quest_nodes_path=quest_nodes_path,
        quest_edges_path=quest_edges_path,
        snapshot_id=query_instance["snapshot_id"],
        scope_values=[
            f"{row['profile']}:{row['physical_side']}"
            for row in query_instance["scopes"]
        ],
        chain_options=chain_options,
        recycling_options=recycling_options,
        construction_chain_options=construction_options,
        max_construction_machine_roots=max_roots,
        validated_query=validated,
        expected_resolution=resolution,
        optional_authorities=True,
    )
    observations = _authority_observations_from_answer(
        answer,
        catalog_available=(
            catalog_root is not None and catalog_root.is_dir()
        ),
        links_available=(
            links_path is not None and links_path.is_file()
        ),
        quest_available=(
            quest_nodes_path is not None
            and quest_nodes_path.is_file()
            and quest_edges_path is not None
            and quest_edges_path.is_file()
        ),
    )
    result = atlas_query.build_query_preflight(
        validated,
        resolution,
        observations,
        question_path=question_path,
        policy_path=policy_path,
    )
    if result["result_status"] == "resolved":
        result["answer"] = answer
        result["result_status"] = answer["result_status"]
        atlas_query.validate_query_result(
            result,
            question_path=question_path,
            policy_path=policy_path,
        )
    return result


def _continuation_targets(
    resolution: atlas_query.QueryResolution,
) -> tuple[ScopedTarget, ...]:
    if resolution.status not in {
        "resolved",
        "resolved-with-profile-gaps",
    }:
        raise CorpusBridgeError(
            "continuation requires an exactly resolved Atlas query"
        )
    return tuple(
        ScopedTarget(
            DomainProfileScope(
                target.scope.profile,
                target.scope.physical_side,
            ),
            target.node,
        )
        for target in resolution.targets
    )


def _continuation_route_section(
    result: dict[str, Any],
) -> dict[str, Any]:
    routes = result["routes"]
    resolved = bool(result["target"].get("resolved"))
    return {
        "status": (
            "answered"
            if routes
            else "no-mechanical-route"
            if resolved
            else "unresolved"
        ),
        "target": result["target"],
        "roots": result["roots"],
        "subproblems": result["subproblems"],
        "items": routes,
        "cycles": result["cycles"],
        "unresolved_leaves": result["unresolved_leaves"],
        "truncation": result["truncation"],
        "evidence_ids": [],
    }


def start_query_continuation(
    *,
    runtime_database: Path,
    query_instance: dict[str, Any],
    evidence: list[dict[str, Any]],
    kind: str,
    max_work_items: int,
    route_manifest: dict[str, Any] | None = None,
    question_path: Path = atlas_query.DEFAULT_QUESTIONS,
    policy_path: Path = atlas_query.DEFAULT_CAPABILITY_POLICY,
) -> dict[str, Any]:
    """Start one opt-in route or recycling continuation lineage."""

    if kind not in {"route", "recycling"}:
        raise CorpusBridgeError(
            f"unsupported continuation kind: {kind}"
        )
    if not isinstance(evidence, list) or any(
        not isinstance(row, dict) for row in evidence
    ):
        raise CorpusBridgeError(
            "continuation evidence must be an array of records"
        )
    with RuntimeGraphReader(runtime_database) as reader:
        validated, resolution = atlas_query.resolve_query_instance(
            reader,
            query_instance,
            question_path=question_path,
            policy_path=policy_path,
        )
        targets = _continuation_targets(resolution)
        route_options, recycling_options, _, _ = _query_chain_options(
            validated.instance
        )
        if kind == "route":
            session: object = RuntimeGraphChainContinuation(
                reader,
                targets,
                route_options,
                target_result={
                    "selector": {
                        "key_kind": validated.instance["selector"][
                            "key_kind"
                        ],
                        "key_value": validated.instance["selector"]["key"],
                        "kind": validated.selector_policy["runtime_kind"],
                    },
                    "resolved": [
                        {
                            "scope": {
                                "profile": target.scope.profile,
                                "physical_side": (
                                    target.scope.physical_side
                                ),
                            },
                            "node": target.node,
                        }
                        for target in resolution.targets
                    ],
                    "gaps": [
                        {
                            "profile": gap.profile,
                            "physical_side": gap.physical_side,
                        }
                        for gap in resolution.profile_gaps
                    ],
                },
                profile_gaps=tuple(
                    DomainProfileScope(
                        gap.profile,
                        gap.physical_side,
                    )
                    for gap in resolution.profile_gaps
                ),
            )
        else:
            if route_manifest is None:
                raise CorpusBridgeError(
                    "recycling continuation requires a complete route manifest"
                )
            atlas_continuation.validate_manifest(route_manifest)
            if (
                route_manifest["kind"] != "route"
                or route_manifest["status"] != "complete"
            ):
                raise CorpusBridgeError(
                    "recycling continuation requires a complete route lineage"
                )
            route_binding = route_manifest["binding"]
            expected_evidence_sha256 = (
                atlas_continuation.canonical_sha256(evidence)
            )
            if (
                route_binding["query_instance_id"]
                != validated.instance["query_instance_id"]
                or route_binding["evidence_sha256"]
                != expected_evidence_sha256
                or route_binding["snapshot_id"]
                != validated.instance["snapshot_id"]
                or route_binding["scopes"]
                != validated.instance["scopes"]
            ):
                raise CorpusBridgeError(
                    "route lineage differs from recycling query/evidence identity"
                )
            routes = _continuation_route_section(
                route_manifest["result"]
            )
            byproducts = _byproduct_payload(routes)
            seeds = _recycling_root_seeds(byproducts)
            if not seeds:
                raise CorpusBridgeError(
                    "complete route lineage has no recycling roots"
                )
            session = RuntimeGraphRecyclingContinuation(
                reader,
                seeds,
                final_targets=_recycling_final_targets(routes),
                route_targets=_recycling_route_targets(routes),
                options=recycling_options,
            )
        return atlas_continuation.start_traversal_continuation(
            session,
            validated.instance,
            evidence,
            max_work_items=max_work_items,
        )


def resume_query_continuation(
    *,
    runtime_database: Path,
    manifest: dict[str, Any],
    max_work_items: int,
) -> dict[str, Any]:
    """Resume one portable manifest against its bound runtime snapshot."""

    with RuntimeGraphReader(runtime_database) as reader:
        return atlas_continuation.resume_traversal_continuation(
            reader,
            manifest,
            max_work_items=max_work_items,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    answer = commands.add_parser(
        "answer",
        help="compose one exact acceptance-question answer",
    )
    answer.add_argument("--runtime-database", type=Path, required=True)
    answer.add_argument(
        "--catalog-root",
        type=Path,
        default=DEFAULT_CATALOG_ROOT,
    )
    answer.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
    )
    answer.add_argument("--question-id", required=True)
    answer.add_argument(
        "--links",
        type=Path,
        default=DEFAULT_PRODUCTION_ROOT / "links.jsonl",
    )
    answer.add_argument(
        "--quest-nodes",
        type=Path,
        default=DEFAULT_PRODUCTION_ROOT / "quest-nodes.jsonl",
    )
    answer.add_argument(
        "--quest-edges",
        type=Path,
        default=DEFAULT_PRODUCTION_ROOT / "quest-edges.jsonl",
    )
    answer.add_argument("--snapshot-id", required=True)
    answer.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="PROFILE:PHYSICAL_SIDE",
    )
    answer.add_argument("--max-depth", type=int, default=8)
    answer.add_argument("--max-routes", type=int, default=50)
    answer.add_argument("--max-alternatives-per-slot", type=int, default=25)
    answer.add_argument("--max-visited-nodes", type=int, default=10000)
    answer.add_argument("--recycling-max-depth", type=int, default=4)
    answer.add_argument("--recycling-max-operations", type=int, default=250)
    answer.add_argument(
        "--recycling-max-consumers-per-target",
        type=int,
        default=25,
    )
    answer.add_argument(
        "--recycling-max-output-alternatives-per-slot",
        type=int,
        default=25,
    )
    answer.add_argument(
        "--recycling-max-visited-targets",
        type=int,
        default=10000,
    )
    answer.add_argument("--construction-max-depth", type=int, default=8)
    answer.add_argument("--construction-max-routes", type=int, default=250)
    answer.add_argument(
        "--construction-max-alternatives-per-slot",
        type=int,
        default=25,
    )
    answer.add_argument(
        "--construction-max-visited-nodes",
        type=int,
        default=10000,
    )
    answer.add_argument(
        "--construction-max-machine-roots",
        type=int,
        default=DEFAULT_MAX_CONSTRUCTION_MACHINE_ROOTS,
    )
    answer.add_argument(
        "--exclude-chanced-outputs",
        action="store_true",
    )
    answer.add_argument(
        "--exclude-procedural-rules",
        action="store_true",
    )
    query = commands.add_parser(
        "query",
        help="compose one explicit target-parameterized Atlas query",
    )
    query.add_argument("--runtime-database", type=Path, required=True)
    query.add_argument("--query-instance", type=Path, required=True)
    query.add_argument(
        "--catalog-root",
        type=Path,
        default=DEFAULT_CATALOG_ROOT,
    )
    query.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
    )
    query.add_argument(
        "--links",
        type=Path,
        default=DEFAULT_PRODUCTION_ROOT / "links.jsonl",
    )
    query.add_argument(
        "--quest-nodes",
        type=Path,
        default=DEFAULT_PRODUCTION_ROOT / "quest-nodes.jsonl",
    )
    query.add_argument(
        "--quest-edges",
        type=Path,
        default=DEFAULT_PRODUCTION_ROOT / "quest-edges.jsonl",
    )
    continuation_start = commands.add_parser(
        "continuation-start",
        help="start an opt-in portable route or recycling continuation",
    )
    continuation_start.add_argument(
        "--runtime-database",
        type=Path,
        required=True,
    )
    continuation_start.add_argument(
        "--query-instance",
        type=Path,
        required=True,
    )
    continuation_start.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help="canonical JSON array of immutable M1 evidence records",
    )
    continuation_start.add_argument(
        "--kind",
        choices=("route", "recycling"),
        required=True,
    )
    continuation_start.add_argument(
        "--work-items",
        type=int,
        required=True,
    )
    continuation_start.add_argument(
        "--route-manifest",
        type=Path,
        help="complete route manifest required for recycling",
    )
    continuation_start.add_argument(
        "--questions",
        type=Path,
        default=atlas_query.DEFAULT_QUESTIONS,
    )
    continuation_start.add_argument(
        "--capability-policy",
        type=Path,
        default=atlas_query.DEFAULT_CAPABILITY_POLICY,
    )
    continuation_resume = commands.add_parser(
        "continuation-resume",
        help="resume an exact portable continuation manifest",
    )
    continuation_resume.add_argument(
        "--runtime-database",
        type=Path,
        required=True,
    )
    continuation_resume.add_argument(
        "--manifest",
        type=Path,
        required=True,
    )
    continuation_resume.add_argument(
        "--work-items",
        type=int,
        required=True,
    )
    continuation_compose = commands.add_parser(
        "continuation-compose",
        help="compose and validate one exact parent/delta pair",
    )
    continuation_compose.add_argument(
        "--parent",
        type=Path,
        required=True,
    )
    continuation_compose.add_argument(
        "--delta",
        type=Path,
        required=True,
    )
    continuation_invalidate = commands.add_parser(
        "continuation-invalidate",
        help="close an active lineage with explicit invalidation reasons",
    )
    continuation_invalidate.add_argument(
        "--parent",
        type=Path,
        required=True,
    )
    continuation_invalidate.add_argument(
        "--reason",
        action="append",
        required=True,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "answer":
            result = build_answer(
                runtime_database=args.runtime_database,
                catalog_root=args.catalog_root,
                question_path=args.questions,
                question_id=args.question_id,
                links_path=args.links,
                quest_nodes_path=args.quest_nodes,
                quest_edges_path=args.quest_edges,
                snapshot_id=args.snapshot_id,
                scope_values=args.scope,
                chain_options=ChainOptions(
                    max_depth=args.max_depth,
                    max_routes=args.max_routes,
                    max_alternatives_per_slot=args.max_alternatives_per_slot,
                    include_chanced_outputs=not args.exclude_chanced_outputs,
                    include_procedural_rules=not args.exclude_procedural_rules,
                    max_visited_nodes=args.max_visited_nodes,
                ),
                recycling_options=RecyclingOptions(
                    max_depth=args.recycling_max_depth,
                    max_operations=args.recycling_max_operations,
                    max_consumers_per_target=(
                        args.recycling_max_consumers_per_target
                    ),
                    max_output_alternatives_per_slot=(
                        args.recycling_max_output_alternatives_per_slot
                    ),
                    max_visited_targets=(
                        args.recycling_max_visited_targets
                    ),
                ),
                construction_chain_options=ChainOptions(
                    max_depth=args.construction_max_depth,
                    max_routes=args.construction_max_routes,
                    max_alternatives_per_slot=(
                        args.construction_max_alternatives_per_slot
                    ),
                    include_chanced_outputs=(
                        not args.exclude_chanced_outputs
                    ),
                    include_procedural_rules=(
                        not args.exclude_procedural_rules
                    ),
                    max_visited_nodes=args.construction_max_visited_nodes,
                ),
                max_construction_machine_roots=(
                    args.construction_max_machine_roots
                ),
            )
        elif args.command == "query":
            instance = _read_json(
                args.query_instance,
                "Atlas query instance",
            )
            if not isinstance(instance, dict):
                raise CorpusBridgeError(
                    "Atlas query instance must be a JSON object"
                )
            result = build_query_answer(
                runtime_database=args.runtime_database,
                query_instance=instance,
                catalog_root=args.catalog_root,
                question_path=args.questions,
                links_path=args.links,
                quest_nodes_path=args.quest_nodes,
                quest_edges_path=args.quest_edges,
            )
        elif args.command == "continuation-start":
            instance = _read_json(
                args.query_instance,
                "Atlas query instance",
            )
            evidence = _read_json(
                args.evidence,
                "Atlas continuation evidence",
            )
            route_manifest = (
                None
                if args.route_manifest is None
                else _read_json(
                    args.route_manifest,
                    "Atlas route continuation manifest",
                )
            )
            if (
                not isinstance(instance, dict)
                or not isinstance(evidence, list)
                or (
                    route_manifest is not None
                    and not isinstance(route_manifest, dict)
                )
            ):
                raise CorpusBridgeError(
                    "continuation start inputs have invalid JSON shapes"
                )
            result = start_query_continuation(
                runtime_database=args.runtime_database,
                query_instance=instance,
                evidence=evidence,
                kind=args.kind,
                max_work_items=args.work_items,
                route_manifest=route_manifest,
                question_path=args.questions,
                policy_path=args.capability_policy,
            )
        elif args.command == "continuation-resume":
            manifest = _read_json(
                args.manifest,
                "Atlas continuation manifest",
            )
            if not isinstance(manifest, dict):
                raise CorpusBridgeError(
                    "continuation manifest must be a JSON object"
                )
            result = resume_query_continuation(
                runtime_database=args.runtime_database,
                manifest=manifest,
                max_work_items=args.work_items,
            )
        elif args.command == "continuation-compose":
            parent = _read_json(
                args.parent,
                "Atlas continuation parent",
            )
            delta = _read_json(
                args.delta,
                "Atlas continuation delta",
            )
            if not isinstance(parent, dict) or not isinstance(delta, dict):
                raise CorpusBridgeError(
                    "continuation compose inputs must be JSON objects"
                )
            result = atlas_continuation.compose_traversal_continuation(
                parent,
                delta,
            )
        elif args.command == "continuation-invalidate":
            parent = _read_json(
                args.parent,
                "Atlas continuation parent",
            )
            if not isinstance(parent, dict):
                raise CorpusBridgeError(
                    "continuation parent must be a JSON object"
                )
            result = (
                atlas_continuation.invalidate_traversal_continuation(
                    parent,
                    sorted(set(args.reason)),
                )
            )
        else:
            raise CorpusBridgeError(
                f"unsupported corpus bridge command: {args.command}"
            )
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except (
        CorpusBridgeError,
        atlas_continuation.AtlasContinuationError,
        atlas_query.AtlasQueryError,
        RuntimeGraphQueryError,
        OSError,
        ValueError,
    ) as exc:
        print(f"corpus bridge invalid: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
