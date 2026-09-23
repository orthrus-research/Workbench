#!/usr/bin/env python3

"""Deterministic fluid-wide mechanical incidence over a runtime graph.

The producer and consumer APIs intentionally hydrate rich occurrence records.
That is the right interface for inspecting one target, but it is unnecessary
work when the question is how every registered fluid is represented.  This
module keeps the same match and owner-eligibility semantics while projecting
only stable counts and classification flags.

The classifier is read-only.  Broad metadata joins are fetched in bounded
chunks, while occurrence joins are deliberately issued for one fluid's small
``target + has_variant`` closure at a time.  This lets SQLite enter through the
``(predicate, object)`` edge index instead of materializing one graph-wide CTE.
The proven V1 classification boundary requires exactly one injectively owned
fluid variant per base fluid and fails closed outside it.
Wall-clock observations belong to callers and never enter the semantic result.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Iterable, Iterator, Sequence

from workbench_atlas.runtime_graph_domain_query import ProfileScope
from workbench_atlas.runtime_graph_query import (
    RuntimeGraphQueryError,
    RuntimeGraphReader,
    decode_record,
)


FLUID_CLASSIFICATION_FORMAT = "workbench-atlas-runtime-fluid-classification-v1"
FLUID_RESOURCE_LOCATION_KEY = "fluid-resource-location"
INCIDENCE_PREDICATES = (
    "produces",
    "may_produce",
    "consumes",
    "may_consume",
    "requires",
)
PRODUCER_PREDICATES = ("produces", "may_produce")
CONSUMING_PREDICATES = ("consumes", "may_consume")
PROCEDURAL_RULE_OWNER_KINDS = (
    "process_rule",
    "recipe_rule",
)
WORLDGEN_GENERATIVE_OWNER_KINDS = (
    "worldgen_deposit",
)
NON_RECIPE_OWNER_KINDS = (
    *PROCEDURAL_RULE_OWNER_KINDS,
    *WORLDGEN_GENERATIVE_OWNER_KINDS,
)
ELIGIBLE_OWNER_KINDS = ("recipe", *NON_RECIPE_OWNER_KINDS)
MATERIAL_IDENTITY_ROLES = (
    "effective_fluid_unifier_lookup",
    "material_fluid_storage_form",
)
MATERIAL_IDENTITY_STATUSES = (
    "no_identity",
    "structural_only",
    "ambiguous_structural_forms",
    "reverse_only",
    "reverse_structural_conflict",
    "reverse_with_conflicting_structural_forms",
    "consistent",
)
INCIDENCE_CLASSIFICATIONS = (
    "bidirectional",
    "source-only",
    "sink-only",
    "isolated",
)
RESOURCE_LOCATION_STATUSES = ("exact", "missing", "ambiguous")
MAX_SQLITE_BOUND_INTEGER = (1 << 63) - 1


@dataclass(frozen=True)
class FluidClassificationBounds:
    """Explicit structural limits for one complete fluid census.

    Defaults are intentionally well above the known 2,448-fluid historical
    projection.  ``metadata_chunk_size`` applies only to direct indexed
    identity/variant lookups.  The smaller ``incidence_match_chunk_size``
    protects the latency-sensitive occurrence join from broad target sets.
    """

    max_fluids: int = 100_000
    max_variants_per_fluid: int = 4_096
    max_match_nodes: int = 1_000_000
    max_identity_keys_per_fluid: int = 256
    max_material_relationships_per_fluid: int = 65_536
    max_incidence_relationships_per_fluid: int = 1_000_000
    metadata_chunk_size: int = 128
    incidence_match_chunk_size: int = 16

    def __post_init__(self) -> None:
        positive = (
            "max_fluids",
            "max_match_nodes",
            "max_identity_keys_per_fluid",
            "max_material_relationships_per_fluid",
            "max_incidence_relationships_per_fluid",
            "metadata_chunk_size",
            "incidence_match_chunk_size",
        )
        non_negative = ("max_variants_per_fluid",)
        for field in positive:
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise RuntimeGraphQueryError(
                    f"fluid classification {field} must be a positive integer"
                )
        for field in non_negative:
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeGraphQueryError(
                    f"fluid classification {field} must be a non-negative integer"
                )
        # ``max_fluids + 1`` is bound into a SQLite LIMIT parameter.  Reject
        # integers that Python's SQLite adapter cannot represent before query
        # execution so callers always receive the domain error type.
        if self.max_fluids >= MAX_SQLITE_BOUND_INTEGER:
            raise RuntimeGraphQueryError(
                "fluid classification max_fluids exceeds the SQLite integer bound"
            )
        # SQLite deployments commonly retain the historical 999-variable
        # default.  No classifier query needs more than this conservative cap.
        for field in ("metadata_chunk_size", "incidence_match_chunk_size"):
            if getattr(self, field) > 900:
                raise RuntimeGraphQueryError(
                    f"fluid classification {field} must not exceed 900"
                )

    def to_dict(self) -> dict[str, int]:
        """Return semantic safety caps, excluding operational batch sizes."""

        return {
            "max_fluids": self.max_fluids,
            "max_variants_per_fluid": self.max_variants_per_fluid,
            "max_match_nodes": self.max_match_nodes,
            "max_identity_keys_per_fluid": self.max_identity_keys_per_fluid,
            "max_material_relationships_per_fluid": (
                self.max_material_relationships_per_fluid
            ),
            "max_incidence_relationships_per_fluid": (
                self.max_incidence_relationships_per_fluid
            ),
        }

    def operational_dict(self) -> dict[str, int]:
        """Return non-semantic query batching controls for instrumentation."""

        return {
            "metadata_chunk_size": self.metadata_chunk_size,
            "incidence_match_chunk_size": self.incidence_match_chunk_size,
        }


DEFAULT_FLUID_CLASSIFICATION_BOUNDS = FluidClassificationBounds()


@dataclass(frozen=True)
class FluidClassificationResult:
    """One canonical, complete classification inside one exact graph scope."""

    scope: ProfileScope
    bounds: FluidClassificationBounds
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FLUID_CLASSIFICATION_FORMAT,
            "scope": self.scope.to_dict(),
            "structural_bounds": self.bounds.to_dict(),
            "fluid_count": len(self.rows),
            "summary": _json_tree(self.summary),
            "rows": _json_tree(self.rows),
        }

    def canonical_bytes(self) -> bytes:
        """Return stable semantic bytes; runtime measurements are not included."""

        try:
            rendered = json.dumps(
                self.to_dict(),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeGraphQueryError(
                f"fluid classification is not canonical JSON: {exc}"
            ) from exc
        return rendered.encode("utf-8")

    @property
    def sha256(self) -> str:
        """Return the lowercase SHA-256 digest of :meth:`canonical_bytes`."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _json_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_tree(child) for child in value]
    return value


def _chunks(values: Sequence[str], size: int) -> Iterator[tuple[str, ...]]:
    for offset in range(0, len(values), size):
        yield tuple(values[offset : offset + size])


def _placeholders(values: Sequence[object]) -> str:
    if not values:
        raise RuntimeGraphQueryError(
            "fluid classification cannot build an empty SQL value list"
        )
    return ",".join("?" for _ in values)


def _material_identity_status(
    reverse_material_ids: set[str],
    form_count: int,
    structural_material_ids: set[str],
) -> str:
    """Mirror ``GtUnificationExtractor.fluidIdentityStatus`` exactly.

    The admitted closure has one exact fluid variant.  Every relationship from
    that variant is retained, and all observed reverse IDs must agree with its
    structural candidates before the result can be classified as consistent.
    """

    reverse_present = bool(reverse_material_ids)
    candidate_count = len(structural_material_ids)
    if not reverse_present and form_count == 0:
        return "no_identity"
    if not reverse_present and candidate_count == 1:
        return "structural_only"
    if not reverse_present:
        return "ambiguous_structural_forms"
    if form_count == 0:
        return "reverse_only"
    if not reverse_material_ids.issubset(structural_material_ids):
        return "reverse_structural_conflict"
    if candidate_count > 1:
        return "reverse_with_conflicting_structural_forms"
    return "consistent"


def _incidence_classification(predicate_counts: dict[str, int]) -> str:
    has_source = any(predicate_counts[predicate] for predicate in PRODUCER_PREDICATES)
    has_sink = any(predicate_counts[predicate] for predicate in CONSUMING_PREDICATES)
    if has_source and has_sink:
        return "bidirectional"
    if has_source:
        return "source-only"
    if has_sink:
        return "sink-only"
    return "isolated"


class RuntimeGraphFluidClassifier:
    """Reusable classifier over one open :class:`RuntimeGraphReader`."""

    def __init__(
        self,
        reader: RuntimeGraphReader,
        bounds: FluidClassificationBounds = DEFAULT_FLUID_CLASSIFICATION_BOUNDS,
    ):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "fluid classification requires an open RuntimeGraphReader"
            )
        if not isinstance(bounds, FluidClassificationBounds):
            raise RuntimeGraphQueryError(
                "fluid classification bounds must be FluidClassificationBounds"
            )
        # Use the reader's public composition seam.  Access also proves that it
        # is still open before any classification state is allocated.
        reader.connection
        self.reader = reader
        self.bounds = bounds

    def _rows(
        self,
        statement: str,
        parameters: Iterable[object] = (),
    ) -> Iterator[tuple[Any, ...]]:
        """Stream query rows and normalize both bind and step failures."""

        try:
            cursor = self.reader.connection.execute(statement, tuple(parameters))
            while True:
                row = cursor.fetchone()
                if row is None:
                    return
                yield tuple(row)
        except (sqlite3.Error, OverflowError) as exc:
            raise RuntimeGraphQueryError(
                f"cannot classify runtime graph fluids: {exc}"
            ) from exc

    def _fluid_ids(self, scope: ProfileScope) -> tuple[str, ...]:
        fluid_ids: list[str] = []
        for row in self._rows(
            "SELECT id FROM nodes "
            "WHERE profile = ? AND physical_side = ? AND kind = 'fluid' "
            "ORDER BY id LIMIT ?",
            (
                scope.profile,
                scope.physical_side,
                self.bounds.max_fluids + 1,
            ),
        ):
            fluid_ids.append(str(row[0]))
            if len(fluid_ids) > self.bounds.max_fluids:
                raise RuntimeGraphQueryError(
                    "fluid classification exceeded max_fluids "
                    f"({self.bounds.max_fluids}) in "
                    f"{scope.profile}/{scope.physical_side}"
                )
        return tuple(fluid_ids)

    def _identity_keys(
        self,
        fluid_ids: tuple[str, ...],
    ) -> dict[str, set[str]]:
        result = {fluid_id: set() for fluid_id in fluid_ids}
        raw_counts = {fluid_id: 0 for fluid_id in fluid_ids}
        for chunk in _chunks(fluid_ids, self.bounds.metadata_chunk_size):
            for node_id, key_value in self._rows(
                "SELECT node_id, key_value FROM node_keys "
                f"WHERE node_id IN ({_placeholders(chunk)}) "
                "AND key_kind = ? ORDER BY node_id, key_value",
                (*chunk, FLUID_RESOURCE_LOCATION_KEY),
            ):
                node_text = str(node_id)
                raw_counts[node_text] += 1
                if raw_counts[node_text] > self.bounds.max_identity_keys_per_fluid:
                    raise RuntimeGraphQueryError(
                        "fluid classification exceeded raw "
                        "max_identity_keys_per_fluid "
                        f"({self.bounds.max_identity_keys_per_fluid}) for "
                        f"{node_text}"
                    )
                values = result[node_text]
                values.add(str(key_value))
                if len(values) > self.bounds.max_identity_keys_per_fluid:
                    raise RuntimeGraphQueryError(
                        "fluid classification exceeded "
                        "max_identity_keys_per_fluid "
                        f"({self.bounds.max_identity_keys_per_fluid}) for "
                        f"{node_id}"
                    )
        return result

    def _variant_closures(
        self,
        fluid_ids: tuple[str, ...],
        scope: ProfileScope,
    ) -> tuple[
        dict[str, set[str]],
        dict[str, set[str]],
        dict[str, set[str]],
    ]:
        variants = {fluid_id: set() for fluid_id in fluid_ids}
        relationship_ids = {fluid_id: set() for fluid_id in fluid_ids}
        raw_counts = {fluid_id: 0 for fluid_id in fluid_ids}
        for chunk in _chunks(fluid_ids, self.bounds.metadata_chunk_size):
            for subject, object_, relationship_id, variant_kind in self._rows(
                "SELECT edge.subject, edge.object, edge.id, variant.kind "
                "FROM edges AS edge "
                "JOIN nodes AS variant ON variant.id = edge.object "
                "WHERE edge.predicate = 'has_variant' "
                f"AND edge.subject IN ({_placeholders(chunk)}) "
                "AND edge.profile = ? AND edge.physical_side = ? "
                "AND variant.profile = ? AND variant.physical_side = ? "
                "ORDER BY edge.subject, edge.object, edge.id",
                (
                    *chunk,
                    scope.profile,
                    scope.physical_side,
                    scope.profile,
                    scope.physical_side,
                ),
            ):
                base_id = str(subject)
                raw_counts[base_id] += 1
                if raw_counts[base_id] > self.bounds.max_variants_per_fluid:
                    raise RuntimeGraphQueryError(
                        "fluid classification exceeded raw "
                        "max_variants_per_fluid "
                        f"({self.bounds.max_variants_per_fluid}) for {base_id}"
                    )
                if str(variant_kind) != "fluid_variant":
                    raise RuntimeGraphQueryError(
                        "fluid classification has_variant target is not a "
                        f"fluid_variant for {base_id}: {object_}"
                    )
                variants[base_id].add(str(object_))
                relationship_ids[base_id].add(str(relationship_id))

        variant_to_fluids: dict[str, set[str]] = {}
        total_match_nodes = 0
        for fluid_id in fluid_ids:
            variant_ids = variants[fluid_id]
            if (
                len(variant_ids) > self.bounds.max_variants_per_fluid
                or len(relationship_ids[fluid_id])
                > self.bounds.max_variants_per_fluid
            ):
                raise RuntimeGraphQueryError(
                    "fluid classification exceeded max_variants_per_fluid "
                    f"({self.bounds.max_variants_per_fluid}) for {fluid_id}"
                )
            if len(variant_ids) != 1:
                raise RuntimeGraphQueryError(
                    "fluid classification requires exactly one distinct "
                    f"fluid_variant for {fluid_id}; found {len(variant_ids)}"
                )
            matches = {fluid_id, *variant_ids}
            total_match_nodes += len(matches)
            if total_match_nodes > self.bounds.max_match_nodes:
                raise RuntimeGraphQueryError(
                    "fluid classification exceeded max_match_nodes "
                    f"({self.bounds.max_match_nodes})"
                )
            variant_id = next(iter(variant_ids))
            variant_to_fluids.setdefault(variant_id, set()).add(fluid_id)
        for variant_id, owners in sorted(variant_to_fluids.items()):
            if len(owners) != 1:
                raise RuntimeGraphQueryError(
                    "fluid classification requires injective fluid_variant "
                    f"ownership; {variant_id} belongs to {len(owners)} fluids"
                )
        return variants, relationship_ids, variant_to_fluids

    def _material_relationships(
        self,
        variant_to_fluids: dict[str, set[str]],
        scope: ProfileScope,
        fluid_ids: tuple[str, ...],
    ) -> dict[str, dict[str, dict[str, str]]]:
        result: dict[str, dict[str, dict[str, str]]] = {
            fluid_id: {} for fluid_id in fluid_ids
        }
        raw_counts = {fluid_id: 0 for fluid_id in fluid_ids}
        variant_ids = tuple(sorted(variant_to_fluids))
        for chunk in _chunks(variant_ids, self.bounds.metadata_chunk_size):
            for subject, object_, relationship_id, encoded in self._rows(
                "SELECT edge.subject, edge.object, edge.id, edge.json "
                "FROM edges AS edge "
                "JOIN nodes AS material ON material.id = edge.object "
                "WHERE edge.predicate = 'has_material' "
                f"AND edge.subject IN ({_placeholders(chunk)}) "
                "AND edge.profile = ? AND edge.physical_side = ? "
                "AND material.profile = ? AND material.physical_side = ? "
                "AND material.kind = 'material' "
                "ORDER BY edge.subject, edge.object, edge.id",
                (
                    *chunk,
                    scope.profile,
                    scope.physical_side,
                    scope.profile,
                    scope.physical_side,
                ),
            ):
                subject_text = str(subject)
                for fluid_id in variant_to_fluids[subject_text]:
                    raw_counts[fluid_id] += 1
                    if (
                        raw_counts[fluid_id]
                        > self.bounds.max_material_relationships_per_fluid
                    ):
                        raise RuntimeGraphQueryError(
                            "fluid classification exceeded raw "
                            "max_material_relationships_per_fluid "
                            f"({self.bounds.max_material_relationships_per_fluid}) "
                            f"for {fluid_id}"
                        )
                relationship = decode_record(str(encoded), "has_material edge")
                attributes = relationship.get("attributes")
                role = attributes.get("role") if isinstance(attributes, dict) else None
                if role not in MATERIAL_IDENTITY_ROLES:
                    continue
                item = {
                    "relationship_id": str(relationship_id),
                    "role": str(role),
                    "subject_id": subject_text,
                    "material_id": str(object_),
                }
                for fluid_id in variant_to_fluids[subject_text]:
                    relationships = result[fluid_id]
                    relationships[item["relationship_id"]] = item
                    if (
                        len(relationships)
                        > self.bounds.max_material_relationships_per_fluid
                    ):
                        raise RuntimeGraphQueryError(
                            "fluid classification exceeded "
                            "max_material_relationships_per_fluid "
                            f"({self.bounds.max_material_relationships_per_fluid}) "
                            f"for {fluid_id}"
                        )
        return result

    def _incidence(
        self,
        match_ids: tuple[str, ...],
        scope: ProfileScope,
        fluid_id: str,
    ) -> tuple[
        dict[str, int],
        dict[str, int],
        tuple[str, ...],
        dict[str, int],
        tuple[str, ...],
    ]:
        occurrences = {predicate: set() for predicate in INCIDENCE_PREDICATES}
        retained_relationship_ids: set[str] = set()
        procedural_rules = {
            predicate: set() for predicate in INCIDENCE_PREDICATES
        }
        worldgen_generative = {
            predicate: set() for predicate in INCIDENCE_PREDICATES
        }
        procedural_rule_owner_kinds: set[str] = set()
        worldgen_generative_owner_kinds: set[str] = set()
        owner_kind_values = ",".join("?" for _ in ELIGIBLE_OWNER_KINDS)
        predicate_values = ",".join("?" for _ in INCIDENCE_PREDICATES)
        raw_count = 0
        chunks = tuple(
            _chunks(match_ids, self.bounds.incidence_match_chunk_size)
        )
        # One proven fluid variant means at most two match chunks.  A slot can
        # match in each chunk, so raw SQL rows are bounded by the retained
        # relationship cap multiplied by that explicit operational fanout.
        max_raw_rows = (
            self.bounds.max_incidence_relationships_per_fluid * len(chunks)
        )

        # A small per-fluid IN list is materially faster on the retained graph
        # than a graph-wide target CTE.  Sets preserve DomainQuery's DISTINCT
        # owner-relationship semantics across match chunks and duplicate
        # accepts-alternative paths.
        for chunk in chunks:
            for relationship_id, predicate, owner_kind in self._rows(
                "SELECT DISTINCT owner_edge.id, owner_edge.predicate, owner.kind "
                "FROM edges AS alternative_edge "
                "JOIN nodes AS slot ON slot.id = alternative_edge.subject "
                "JOIN edges AS owner_edge ON owner_edge.object = slot.id "
                "JOIN nodes AS owner ON owner.id = owner_edge.subject "
                "WHERE alternative_edge.predicate = 'accepts_alternative' "
                f"AND alternative_edge.object IN ({_placeholders(chunk)}) "
                f"AND owner_edge.predicate IN ({predicate_values}) "
                "AND alternative_edge.profile = ? "
                "AND alternative_edge.physical_side = ? "
                "AND slot.profile = ? AND slot.physical_side = ? "
                "AND owner_edge.profile = ? AND owner_edge.physical_side = ? "
                "AND owner.profile = ? AND owner.physical_side = ? "
                f"AND owner.kind IN ({owner_kind_values}) "
                "AND (owner.kind != 'recipe' OR ("
                "owner.profile = 'COMMON_FINAL_STATE' AND EXISTS ("
                "SELECT 1 FROM edges AS membership "
                "WHERE membership.object = owner.id "
                "AND membership.predicate = 'has_recipe' "
                "AND membership.profile = owner.profile "
                "AND membership.physical_side = owner.physical_side "
                "AND json_extract("
                "membership.json, '$.attributes.lookup_active'"
                ") = 1)))",
                (
                    *chunk,
                    *INCIDENCE_PREDICATES,
                    scope.profile,
                    scope.physical_side,
                    scope.profile,
                    scope.physical_side,
                    scope.profile,
                    scope.physical_side,
                    scope.profile,
                    scope.physical_side,
                    *ELIGIBLE_OWNER_KINDS,
                ),
            ):
                raw_count += 1
                if raw_count > max_raw_rows:
                    raise RuntimeGraphQueryError(
                        "fluid classification exceeded raw incidence row bound "
                        f"({max_raw_rows}) for {fluid_id}"
                    )
                predicate_text = str(predicate)
                relationship_text = str(relationship_id)
                occurrences[predicate_text].add(relationship_text)
                retained_relationship_ids.add(relationship_text)
                owner_kind_text = str(owner_kind)
                if owner_kind_text in PROCEDURAL_RULE_OWNER_KINDS:
                    procedural_rules[predicate_text].add(relationship_text)
                    procedural_rule_owner_kinds.add(owner_kind_text)
                elif owner_kind_text in WORLDGEN_GENERATIVE_OWNER_KINDS:
                    worldgen_generative[predicate_text].add(relationship_text)
                    worldgen_generative_owner_kinds.add(owner_kind_text)
                if (
                    len(retained_relationship_ids)
                    > self.bounds.max_incidence_relationships_per_fluid
                ):
                    raise RuntimeGraphQueryError(
                        "fluid classification exceeded retained "
                        "max_incidence_relationships_per_fluid "
                        f"({self.bounds.max_incidence_relationships_per_fluid}) "
                        f"for {fluid_id}"
                    )

        return (
            {
                predicate: len(occurrences[predicate])
                for predicate in INCIDENCE_PREDICATES
            },
            {
                predicate: len(procedural_rules[predicate])
                for predicate in INCIDENCE_PREDICATES
            },
            tuple(sorted(procedural_rule_owner_kinds)),
            {
                predicate: len(worldgen_generative[predicate])
                for predicate in INCIDENCE_PREDICATES
            },
            tuple(sorted(worldgen_generative_owner_kinds)),
        )

    @staticmethod
    def _material_projection(
        relationships_by_id: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        relationships = tuple(
            sorted(
                relationships_by_id.values(),
                key=lambda row: (
                    row["role"],
                    row["subject_id"],
                    row["material_id"],
                    row["relationship_id"],
                ),
            )
        )
        reverse = tuple(
            row
            for row in relationships
            if row["role"] == "effective_fluid_unifier_lookup"
        )
        structural = tuple(
            row
            for row in relationships
            if row["role"] == "material_fluid_storage_form"
        )
        reverse_material_ids = {row["material_id"] for row in reverse}
        structural_material_ids = {row["material_id"] for row in structural}
        return {
            "status": _material_identity_status(
                reverse_material_ids,
                len(structural),
                structural_material_ids,
            ),
            "form_count": len(structural),
            "candidate_material_count": len(structural_material_ids),
            "reverse_present": bool(reverse),
            "reverse_among_candidates": bool(reverse)
            and reverse_material_ids.issubset(structural_material_ids),
            "reverse": {
                "role": "effective_fluid_unifier_lookup",
                "relationship_count": len(reverse),
                "relationship_ids": sorted(
                    row["relationship_id"] for row in reverse
                ),
                "material_ids": sorted(reverse_material_ids),
            },
            "structural_forms": {
                "role": "material_fluid_storage_form",
                "relationship_count": len(structural),
                "relationship_ids": sorted(
                    row["relationship_id"] for row in structural
                ),
                "material_ids": sorted(structural_material_ids),
            },
            "relationships": list(relationships),
        }

    def classify(self, scope: ProfileScope) -> FluidClassificationResult:
        """Classify every exact fluid node in one explicit profile scope."""

        if not isinstance(scope, ProfileScope):
            raise RuntimeGraphQueryError(
                "fluid classification requires one explicit ProfileScope"
            )
        fluid_ids = self._fluid_ids(scope)
        identity_keys = self._identity_keys(fluid_ids)
        variants, variant_edges, variant_to_fluids = self._variant_closures(
            fluid_ids,
            scope,
        )
        material_relationships = self._material_relationships(
            variant_to_fluids,
            scope,
            fluid_ids,
        )

        rows: list[dict[str, Any]] = []
        for fluid_id in fluid_ids:
            key_values = sorted(identity_keys[fluid_id])
            if not key_values:
                key_status = "missing"
                exact_key: str | None = None
            elif len(key_values) == 1:
                key_status = "exact"
                exact_key = key_values[0]
            else:
                key_status = "ambiguous"
                exact_key = None

            variant_ids = sorted(variants[fluid_id])
            match_ids = tuple(sorted({fluid_id, *variant_ids}))
            (
                predicate_counts,
                procedural_rule_counts,
                procedural_rule_owner_kinds,
                worldgen_generative_counts,
                worldgen_generative_owner_kinds,
            ) = self._incidence(match_ids, scope, fluid_id)
            classification = _incidence_classification(predicate_counts)
            producer_count = sum(
                predicate_counts[predicate] for predicate in PRODUCER_PREDICATES
            )
            consumer_count = sum(
                predicate_counts[predicate] for predicate in CONSUMING_PREDICATES
            )
            material = self._material_projection(
                material_relationships[fluid_id]
            )
            rows.append(
                {
                    "fluid_id": fluid_id,
                    "fluid_resource_location": exact_key,
                    "fluid_resource_location_status": key_status,
                    "fluid_resource_location_values": key_values,
                    "match_closure": {
                        "target_id": fluid_id,
                        "node_ids": list(match_ids),
                        "variant_node_ids": variant_ids,
                        "has_variant_relationship_ids": sorted(
                            variant_edges[fluid_id]
                        ),
                    },
                    "material_identity": material,
                    "incidence": {
                        "classification": classification,
                        "predicate_counts": predicate_counts,
                        "producer_count": producer_count,
                        "consumer_count": consumer_count,
                        "requirement_count": predicate_counts["requires"],
                        "procedural_rule_predicate_counts": (
                            procedural_rule_counts
                        ),
                        "procedural_rule_owner_kinds": list(
                            procedural_rule_owner_kinds
                        ),
                        "worldgen_generative_predicate_counts": (
                            worldgen_generative_counts
                        ),
                        "worldgen_generative_owner_kinds": list(
                            worldgen_generative_owner_kinds
                        ),
                        "flags": {
                            "reusable": predicate_counts["requires"] > 0,
                            "procedural_rule": any(
                                procedural_rule_counts.values()
                            ),
                            "procedural_rule_producer": any(
                                procedural_rule_counts[predicate]
                                for predicate in PRODUCER_PREDICATES
                            ),
                            "procedural_rule_consumer": any(
                                procedural_rule_counts[predicate]
                                for predicate in CONSUMING_PREDICATES
                            ),
                            "procedural_rule_requirement": (
                                procedural_rule_counts["requires"] > 0
                            ),
                            "worldgen_generative": any(
                                worldgen_generative_counts.values()
                            ),
                            "worldgen_generative_producer": any(
                                worldgen_generative_counts[predicate]
                                for predicate in PRODUCER_PREDICATES
                            ),
                            "worldgen_generative_consumer": any(
                                worldgen_generative_counts[predicate]
                                for predicate in CONSUMING_PREDICATES
                            ),
                            "worldgen_generative_requirement": (
                                worldgen_generative_counts["requires"] > 0
                            ),
                        },
                    },
                }
            )

        classification_counts = {
            classification: 0 for classification in INCIDENCE_CLASSIFICATIONS
        }
        material_status_counts = {
            status: 0 for status in MATERIAL_IDENTITY_STATUSES
        }
        resource_status_counts = {
            status: 0 for status in RESOURCE_LOCATION_STATUSES
        }
        predicate_totals = {predicate: 0 for predicate in INCIDENCE_PREDICATES}
        procedural_rule_predicate_totals = {
            predicate: 0 for predicate in INCIDENCE_PREDICATES
        }
        worldgen_generative_predicate_totals = {
            predicate: 0 for predicate in INCIDENCE_PREDICATES
        }
        reusable_fluid_count = 0
        procedural_rule_fluid_count = 0
        worldgen_generative_fluid_count = 0
        for row in rows:
            incidence = row["incidence"]
            classification_counts[incidence["classification"]] += 1
            material_status_counts[row["material_identity"]["status"]] += 1
            resource_status_counts[row["fluid_resource_location_status"]] += 1
            for predicate in INCIDENCE_PREDICATES:
                predicate_totals[predicate] += incidence["predicate_counts"][
                    predicate
                ]
                procedural_rule_predicate_totals[predicate] += incidence[
                    "procedural_rule_predicate_counts"
                ][predicate]
                worldgen_generative_predicate_totals[predicate] += incidence[
                    "worldgen_generative_predicate_counts"
                ][predicate]
            reusable_fluid_count += int(incidence["flags"]["reusable"])
            procedural_rule_fluid_count += int(
                incidence["flags"]["procedural_rule"]
            )
            worldgen_generative_fluid_count += int(
                incidence["flags"]["worldgen_generative"]
            )

        summary = {
            "incidence_classification_counts": classification_counts,
            "predicate_occurrence_counts": predicate_totals,
            "material_identity_status_counts": material_status_counts,
            "fluid_resource_location_status_counts": resource_status_counts,
            "reusable_fluid_count": reusable_fluid_count,
            "procedural_rule_fluid_count": procedural_rule_fluid_count,
            "procedural_rule_occurrence_count": sum(
                procedural_rule_predicate_totals.values()
            ),
            "procedural_rule_predicate_occurrence_counts": (
                procedural_rule_predicate_totals
            ),
            "worldgen_generative_fluid_count": (
                worldgen_generative_fluid_count
            ),
            "worldgen_generative_occurrence_count": sum(
                worldgen_generative_predicate_totals.values()
            ),
            "worldgen_generative_predicate_occurrence_counts": (
                worldgen_generative_predicate_totals
            ),
        }
        return FluidClassificationResult(
            scope=scope,
            bounds=self.bounds,
            rows=tuple(rows),
            summary=summary,
        )


def classify_fluids(
    reader: RuntimeGraphReader,
    scope: ProfileScope,
    bounds: FluidClassificationBounds = DEFAULT_FLUID_CLASSIFICATION_BOUNDS,
) -> FluidClassificationResult:
    """Convenience wrapper for one complete, scope-bound fluid census."""

    return RuntimeGraphFluidClassifier(reader, bounds).classify(scope)
