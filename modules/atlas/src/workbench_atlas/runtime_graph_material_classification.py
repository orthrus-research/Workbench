#!/usr/bin/env python3

"""Material-first classification over an immutable Atlas runtime graph.

The runtime graph records materials before any consumer projection chooses a
fluid, item, recipe, or worldgen entry as its target.  This module preserves
that boundary.  It classifies the frozen material core, then attaches prefix
generation decisions and observed forms as separate lenses.

Platform semantics are supplied by :class:`MaterialClassificationPolicy`.
The generic Atlas module never assumes that GTCEu property names or closure
rules apply to another platform or pack profile.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Sequence

from workbench_api.material_classification import (
    LegacyMaterialPolicySnapshot,
    MaterialClassificationPolicy,
    MaterialPolicyValidationError,
    admit_material_classification_policy,
)
from workbench_atlas.runtime_graph_domain_query import ProfileScope
from workbench_atlas.runtime_graph_query import (
    RuntimeGraphQueryError,
    RuntimeGraphReader,
    decode_record,
)


MATERIAL_CLASSIFICATION_FORMAT = (
    "workbench-atlas-runtime-material-classification-v1"
)
MATERIAL_IDENTITY_KEY_KINDS = (
    "material-numeric-id",
    "material-resource-location",
)
MATERIAL_REGISTRY_ROLES = ("marker", "persistent")
COMPOSITION_BASES = (
    "composite",
    "elemental",
    "elemental_composite",
    "unspecified",
)
CLASSIFICATION_STATUSES = ("exact", "frontier")
REALIZED_FORM_KINDS = ("fluid", "item_variant", "other")
MAX_SQLITE_BOUND_INTEGER = (1 << 63) - 1


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeGraphQueryError(
            f"material classification is not canonical JSON: {exc}"
        ) from exc


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


@dataclass(frozen=True)
class MaterialClassificationBounds:
    """Fail-closed structural limits for one material census."""

    max_materials: int = 100_000
    max_identity_keys_per_material: int = 256
    max_properties_per_material: int = 512
    max_components_per_material: int = 4_096
    max_elements_per_material: int = 16
    max_realized_forms_per_material: int = 65_536
    max_generation_decisions_per_material: int = 65_536
    metadata_chunk_size: int = 128

    def __post_init__(self) -> None:
        for field in (
            "max_materials",
            "max_identity_keys_per_material",
            "max_properties_per_material",
            "max_components_per_material",
            "max_elements_per_material",
            "max_realized_forms_per_material",
            "max_generation_decisions_per_material",
            "metadata_chunk_size",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise RuntimeGraphQueryError(
                    f"material classification {field} must be a positive integer"
                )
        if self.max_materials >= MAX_SQLITE_BOUND_INTEGER:
            raise RuntimeGraphQueryError(
                "material classification max_materials exceeds the SQLite "
                "integer bound"
            )
        if self.metadata_chunk_size > 896:
            raise RuntimeGraphQueryError(
                "material classification metadata_chunk_size must not exceed 896"
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_materials": self.max_materials,
            "max_identity_keys_per_material": (
                self.max_identity_keys_per_material
            ),
            "max_properties_per_material": self.max_properties_per_material,
            "max_components_per_material": self.max_components_per_material,
            "max_elements_per_material": self.max_elements_per_material,
            "max_realized_forms_per_material": (
                self.max_realized_forms_per_material
            ),
            "max_generation_decisions_per_material": (
                self.max_generation_decisions_per_material
            ),
        }


DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS = MaterialClassificationBounds()


@dataclass(frozen=True)
class MaterialClassificationResult:
    """Canonical material inventory and independently queryable lenses."""

    scope: ProfileScope
    policy: MaterialClassificationPolicy
    bounds: MaterialClassificationBounds
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MATERIAL_CLASSIFICATION_FORMAT,
            "scope": self.scope.to_dict(),
            "policy": {
                **self.policy.to_dict(),
                "sha256": self.policy.sha256,
            },
            "structural_bounds": self.bounds.to_dict(),
            "material_count": len(self.rows),
            "summary": _json_tree(self.summary),
            "materials": _json_tree(self.rows),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def find_materials(
        self,
        key_kind: str,
        key_value: str,
    ) -> tuple[dict[str, Any], ...]:
        key_kind = _required_text(key_kind, "material classification key kind")
        key_value = _required_text(key_value, "material classification key value")
        if key_kind == "runtime-node-id":
            return tuple(
                row for row in self.rows if row["material_id"] == key_value
            )
        if key_kind not in MATERIAL_IDENTITY_KEY_KINDS:
            raise RuntimeGraphQueryError(
                f"unsupported material classification key kind: {key_kind}"
            )
        return tuple(
            row
            for row in self.rows
            if key_value in row["core"]["identity"]["observed_keys"][key_kind]
        )

    def by_registry_role(self, role: str) -> tuple[dict[str, Any], ...]:
        role = _required_text(role, "material registry role")
        if role not in MATERIAL_REGISTRY_ROLES:
            raise RuntimeGraphQueryError(
                f"unsupported material registry role: {role}"
            )
        return tuple(
            row for row in self.rows if row["core"]["registry_role"] == role
        )

    def by_property(self, key: str) -> tuple[dict[str, Any], ...]:
        key = _required_text(key, "material property key")
        return tuple(
            row
            for row in self.rows
            if key in row["core"]["properties"]["keys"]
        )

    def by_composition_basis(self, basis: str) -> tuple[dict[str, Any], ...]:
        basis = _required_text(basis, "material composition basis")
        if basis not in COMPOSITION_BASES:
            raise RuntimeGraphQueryError(
                f"unsupported material composition basis: {basis}"
            )
        return tuple(
            row
            for row in self.rows
            if row["core"]["composition"]["basis"] == basis
        )

    def frontier(
        self,
        issue_code: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if issue_code is not None:
            issue_code = _required_text(
                issue_code,
                "material classification issue code",
            )
        return tuple(
            row
            for row in self.rows
            if row["frontier_issue_codes"]
            and (
                issue_code is None
                or issue_code in row["frontier_issue_codes"]
            )
        )


class RuntimeGraphMaterialClassification:
    """Build one policy-bound material classification from an open graph."""

    def __init__(
        self,
        reader: RuntimeGraphReader,
        policy: MaterialClassificationPolicy | LegacyMaterialPolicySnapshot,
        bounds: MaterialClassificationBounds = (
            DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS
        ),
    ):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "material classification requires an open RuntimeGraphReader"
            )
        try:
            admitted_policy = admit_material_classification_policy(policy)
        except MaterialPolicyValidationError as exc:
            raise RuntimeGraphQueryError(str(exc)) from exc
        if not isinstance(bounds, MaterialClassificationBounds):
            raise RuntimeGraphQueryError(
                "material classification bounds must be "
                "MaterialClassificationBounds"
            )
        reader.connection
        self.reader = reader
        self.policy = admitted_policy
        self.bounds = bounds

    def _rows(
        self,
        statement: str,
        parameters: Iterable[object] = (),
    ) -> Iterator[tuple[Any, ...]]:
        try:
            cursor = self.reader.connection.execute(statement, tuple(parameters))
            while True:
                row = cursor.fetchone()
                if row is None:
                    return
                yield tuple(row)
        except (sqlite3.Error, OverflowError) as exc:
            raise RuntimeGraphQueryError(
                f"cannot classify runtime materials: {exc}"
            ) from exc

    def _material_nodes(
        self,
        scope: ProfileScope,
    ) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for identifier, adapter, encoded in self._rows(
            "SELECT id, adapter, json FROM nodes "
            "WHERE profile = ? AND physical_side = ? AND kind = 'material' "
            "ORDER BY id LIMIT ?",
            (
                scope.profile,
                scope.physical_side,
                self.bounds.max_materials + 1,
            ),
        ):
            if len(rows) >= self.bounds.max_materials:
                raise RuntimeGraphQueryError(
                    "material classification exceeded max_materials "
                    f"({self.bounds.max_materials}) in "
                    f"{scope.profile}/{scope.physical_side}"
                )
            adapter = str(adapter)
            if adapter not in self.policy.admitted_material_adapters:
                raise RuntimeGraphQueryError(
                    "material classification encountered an unadmitted material "
                    f"adapter: {adapter}"
                )
            node = decode_record(str(encoded), "material node")
            if (
                node.get("id") != identifier
                or node.get("kind") != "material"
                or not isinstance(node.get("attributes"), dict)
                or not isinstance(node.get("scope"), dict)
                or node["scope"].get("profile") != scope.profile
                or node["scope"].get("physical_side") != scope.physical_side
                or node["scope"].get("adapter") != adapter
            ):
                raise RuntimeGraphQueryError(
                    f"material node scope or identity differs: {identifier}"
                )
            rows.append(
                {
                    "material_id": str(identifier),
                    "adapter": adapter,
                    "node": node,
                }
            )
        if not rows:
            raise RuntimeGraphQueryError(
                "material classification found no material nodes in its exact scope"
            )
        return tuple(rows)

    def _identity_keys(
        self,
        material_ids: tuple[str, ...],
    ) -> dict[str, dict[str, list[str]]]:
        result = {
            material_id: {kind: [] for kind in MATERIAL_IDENTITY_KEY_KINDS}
            for material_id in material_ids
        }
        raw_counts = {material_id: 0 for material_id in material_ids}
        for chunk in self._chunks(material_ids):
            placeholders = ",".join("?" for _ in chunk)
            kinds = ",".join("?" for _ in MATERIAL_IDENTITY_KEY_KINDS)
            for node_id, key_kind, key_value in self._rows(
                "SELECT node_id, key_kind, key_value FROM node_keys "
                f"WHERE node_id IN ({placeholders}) "
                f"AND key_kind IN ({kinds}) "
                "ORDER BY node_id, key_kind, key_value",
                (*chunk, *MATERIAL_IDENTITY_KEY_KINDS),
            ):
                material_id = str(node_id)
                raw_counts[material_id] += 1
                if (
                    raw_counts[material_id]
                    > self.bounds.max_identity_keys_per_material
                ):
                    raise RuntimeGraphQueryError(
                        "material classification exceeded "
                        "max_identity_keys_per_material for "
                        f"{material_id}"
                    )
                values = result[material_id][str(key_kind)]
                value = str(key_value)
                if value not in values:
                    values.append(value)
        return result

    def _chunks(self, values: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        size = self.bounds.metadata_chunk_size
        for offset in range(0, len(values), size):
            yield values[offset : offset + size]

    def _outgoing_lens(
        self,
        scope: ProfileScope,
        material_ids: tuple[str, ...],
        predicate: str,
        limit_field: str,
    ) -> dict[str, list[dict[str, Any]]]:
        result = {material_id: [] for material_id in material_ids}
        maximum = int(getattr(self.bounds, limit_field))
        for chunk in self._chunks(material_ids):
            placeholders = ",".join("?" for _ in chunk)
            for subject, edge_id, edge_adapter, edge_json, object_id, kind, node_json in self._rows(
                "SELECT edge.subject, edge.id, edge.adapter, edge.json, "
                "target.id, target.kind, target.json "
                "FROM edges AS edge JOIN nodes AS target "
                "ON target.id = edge.object "
                f"WHERE edge.subject IN ({placeholders}) "
                "AND edge.profile = ? AND edge.physical_side = ? "
                "AND edge.predicate = ? "
                "ORDER BY edge.subject, edge.id",
                (*chunk, scope.profile, scope.physical_side, predicate),
            ):
                material_id = str(subject)
                rows = result[material_id]
                if len(rows) >= maximum:
                    raise RuntimeGraphQueryError(
                        f"material classification exceeded {limit_field} for "
                        f"{material_id}"
                    )
                edge = decode_record(str(edge_json), f"{predicate} relationship")
                node = decode_record(str(node_json), f"{predicate} target")
                if (
                    edge.get("id") != edge_id
                    or edge.get("subject") != material_id
                    or edge.get("object") != object_id
                    or edge.get("predicate") != predicate
                    or node.get("id") != object_id
                    or node.get("kind") != kind
                ):
                    raise RuntimeGraphQueryError(
                        f"material {predicate} relationship differs: {edge_id}"
                    )
                rows.append(
                    {
                        "relationship_id": str(edge_id),
                        "relationship_adapter": str(edge_adapter),
                        "relationship_attributes": edge.get("attributes"),
                        "node_id": str(object_id),
                        "node_kind": str(kind),
                        "node_attributes": node.get("attributes"),
                    }
                )
        return result

    def _generation_decisions(
        self,
        scope: ProfileScope,
        material_ids: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        result = {material_id: [] for material_id in material_ids}
        constraint_kind = self.policy.generation_constraint_kind
        if constraint_kind is None:
            return result
        for chunk in self._chunks(material_ids):
            placeholders = ",".join("?" for _ in chunk)
            for row in self._rows(
                "SELECT material_edge.object, material_edge.id, "
                "material_edge.json, constraint_node.id, constraint_node.json, "
                "prefix_edge.id, prefix_edge.json, prefix.id, prefix.json "
                "FROM edges AS material_edge "
                "JOIN nodes AS constraint_node "
                "ON constraint_node.id = material_edge.subject "
                "JOIN edges AS prefix_edge "
                "ON prefix_edge.object = constraint_node.id "
                "AND prefix_edge.predicate = 'has_constraint' "
                "JOIN nodes AS prefix ON prefix.id = prefix_edge.subject "
                f"WHERE material_edge.object IN ({placeholders}) "
                "AND material_edge.profile = ? "
                "AND material_edge.physical_side = ? "
                "AND material_edge.predicate = 'has_material' "
                "AND constraint_node.kind = 'constraint' "
                "AND prefix.kind = 'ore_prefix' "
                "ORDER BY material_edge.object, constraint_node.id, prefix.id",
                (*chunk, scope.profile, scope.physical_side),
            ):
                (
                    material_id,
                    material_edge_id,
                    material_edge_json,
                    constraint_id,
                    constraint_json,
                    prefix_edge_id,
                    prefix_edge_json,
                    prefix_id,
                    prefix_json,
                ) = row
                material_id = str(material_id)
                material_edge = decode_record(
                    str(material_edge_json),
                    "material generation relationship",
                )
                constraint = decode_record(
                    str(constraint_json),
                    "material generation constraint",
                )
                prefix_edge = decode_record(
                    str(prefix_edge_json),
                    "ore-prefix generation relationship",
                )
                prefix = decode_record(str(prefix_json), "ore-prefix node")
                attributes = constraint.get("attributes")
                if not isinstance(attributes, dict):
                    raise RuntimeGraphQueryError(
                        f"material generation constraint is malformed: {constraint_id}"
                    )
                if attributes.get("constraint_kind") != constraint_kind:
                    continue
                material_edge_attributes = material_edge.get("attributes")
                if (
                    material_edge.get("id") != material_edge_id
                    or material_edge.get("object") != material_id
                    or material_edge.get("subject") != constraint_id
                    or not isinstance(material_edge_attributes, dict)
                    or material_edge_attributes.get("relationship")
                    != "evaluated_material"
                    or prefix_edge.get("id") != prefix_edge_id
                    or prefix_edge.get("object") != constraint_id
                    or prefix_edge.get("subject") != prefix_id
                    or prefix.get("id") != prefix_id
                    or attributes.get("material_node_id") != material_id
                ):
                    raise RuntimeGraphQueryError(
                        f"material generation decision identity differs: {constraint_id}"
                    )
                rows = result[material_id]
                if len(rows) >= self.bounds.max_generation_decisions_per_material:
                    raise RuntimeGraphQueryError(
                        "material classification exceeded "
                        "max_generation_decisions_per_material for "
                        f"{material_id}"
                    )
                prefix_attributes = prefix.get("attributes")
                prefix_name = attributes.get("prefix_name")
                if (
                    not isinstance(prefix_name, str)
                    or not prefix_name
                    or not isinstance(prefix_attributes, dict)
                    or prefix_attributes.get("name") != prefix_name
                    or not isinstance(attributes.get("do_generate_item"), bool)
                    or not isinstance(attributes.get("is_ignored"), bool)
                ):
                    raise RuntimeGraphQueryError(
                        f"material generation decision is malformed: {constraint_id}"
                    )
                rows.append(
                    {
                        "constraint_node_id": str(constraint_id),
                        "material_relationship_id": str(material_edge_id),
                        "prefix_relationship_id": str(prefix_edge_id),
                        "ore_prefix_id": str(prefix_id),
                        "prefix_name": prefix_name,
                        "do_generate_item": attributes["do_generate_item"],
                        "is_ignored": attributes["is_ignored"],
                        "effective_material_amount": attributes.get(
                            "effective_material_amount"
                        ),
                        "is_amount_modified": attributes.get(
                            "is_amount_modified"
                        ),
                    }
                )
        return result

    @staticmethod
    def _require_attribute(
        attributes: Mapping[str, Any],
        name: str,
        expected: type,
        material_id: str,
    ) -> Any:
        value = attributes.get(name)
        if expected is int:
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = isinstance(value, expected)
        if not valid:
            raise RuntimeGraphQueryError(
                f"material attribute {name} is invalid for {material_id}"
            )
        return value

    def _property_lens(
        self,
        material_id: str,
        registry_name: str,
        relations: list[dict[str, Any]],
        issues: set[str],
    ) -> dict[str, Any]:
        properties: list[dict[str, Any]] = []
        keys: list[str] = []
        for relation in relations:
            if relation["node_kind"] != "material_property":
                issues.add("property:target-kind")
                continue
            edge = relation["relationship_attributes"]
            attributes = relation["node_attributes"]
            if not isinstance(edge, dict) or not isinstance(attributes, dict):
                raise RuntimeGraphQueryError(
                    f"material property relationship is malformed: {material_id}"
                )
            key = attributes.get("key")
            if (
                not isinstance(key, str)
                or not key
                or edge.get("key") != key
                or attributes.get("material") != registry_name
            ):
                issues.add("property:identity-conflict")
                continue
            keys.append(key)
            properties.append(
                {
                    "key": key,
                    "property_node_id": relation["node_id"],
                    "relationship_id": relation["relationship_id"],
                    "implementation_class": attributes.get(
                        "implementation_class"
                    ),
                    "raw_value_null": attributes.get("raw_value_null"),
                    "value": attributes.get("value"),
                }
            )
        properties.sort(
            key=lambda row: (
                row["key"].encode("utf-8"),
                row["property_node_id"].encode("utf-8"),
            )
        )
        unique_keys = sorted(set(keys), key=lambda value: value.encode("utf-8"))
        if len(unique_keys) != len(keys):
            issues.add("property:duplicate-key")
        base = [key for key in unique_keys if key in self.policy.base_property_keys]
        capability = [
            key
            for key in unique_keys
            if key in self.policy.capability_property_keys
        ]
        extensions = [
            key
            for key in unique_keys
            if key not in self.policy.base_property_keys
            and key not in self.policy.capability_property_keys
        ]
        return {
            "keys": unique_keys,
            "base_keys": base,
            "capability_keys": capability,
            "profile_extension_keys": extensions,
            "verified_values": properties,
        }

    def _property_closure_issues(
        self,
        registry_role: str,
        keys: set[str],
    ) -> set[str]:
        issues: set[str] = set()
        if registry_role == "persistent" and not (
            keys & set(self.policy.base_property_keys)
        ):
            issues.add("property:missing-base")
        for key, required in self.policy.property_dependencies:
            if key in keys:
                for dependency in required:
                    if dependency not in keys:
                        issues.add(f"property:{key}:missing:{dependency}")
        for key, alternatives in self.policy.property_any_dependencies:
            if key in keys and not (keys & set(alternatives)):
                issues.add(
                    "property:"
                    + key
                    + ":missing-any:"
                    + "|".join(alternatives)
                )
        for left, right in self.policy.property_incompatibilities:
            if left in keys and right in keys:
                issues.add(f"property:incompatible:{left}|{right}")
        return issues

    def _flag_lens(
        self,
        raw_flags: list[Any],
        property_keys: set[str],
        issues: set[str],
    ) -> dict[str, Any]:
        if any(not isinstance(flag, str) or not flag for flag in raw_flags):
            raise RuntimeGraphQueryError("material flag set contains an invalid name")
        names = sorted(set(raw_flags), key=lambda value: value.encode("utf-8"))
        if len(names) != len(raw_flags):
            issues.add("flag:duplicate-name")
        categories = dict(self.policy.flag_categories)
        names_set = set(names)
        for property_key, implied in self.policy.property_implied_flags:
            if property_key not in property_keys:
                continue
            for flag in implied:
                if flag not in names_set:
                    issues.add(
                        f"flag:{flag}:missing-implied-by-property:{property_key}"
                    )
        for flag, required in self.policy.flag_dependencies:
            if flag not in names_set:
                continue
            for dependency in required:
                if dependency not in names_set:
                    issues.add(f"flag:{flag}:missing:{dependency}")
        for flag, required in self.policy.flag_property_requirements:
            if flag not in names_set:
                continue
            for property_key in required:
                if property_key not in property_keys:
                    issues.add(
                        f"flag:{flag}:missing-property:{property_key}"
                    )
        rows = [
            {
                "name": name,
                "categories": list(categories.get(name, ("profile_extension",))),
            }
            for name in names
        ]
        by_category: dict[str, list[str]] = {}
        for row in rows:
            for category in row["categories"]:
                by_category.setdefault(category, []).append(row["name"])
        return {
            "names": names,
            "classified": rows,
            "by_category": {
                category: by_category[category]
                for category in sorted(by_category, key=lambda value: value.encode("utf-8"))
            },
        }

    @staticmethod
    def _composition_lens(
        attributes: Mapping[str, Any],
        components: list[dict[str, Any]],
        elements: list[dict[str, Any]],
        issues: set[str],
    ) -> dict[str, Any]:
        component_rows: list[dict[str, Any]] = []
        ordinals: list[int] = []
        for relation in components:
            edge = relation["relationship_attributes"]
            if relation["node_kind"] != "material" or not isinstance(edge, dict):
                issues.add("composition:component-target")
                continue
            amount = edge.get("amount")
            ordinal = edge.get("ordinal")
            if (
                not isinstance(amount, int)
                or isinstance(amount, bool)
                or not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or ordinal < 0
            ):
                issues.add("composition:component-shape")
                continue
            ordinals.append(ordinal)
            component_rows.append(
                {
                    "material_id": relation["node_id"],
                    "amount": amount,
                    "ordinal": ordinal,
                    "relationship_id": relation["relationship_id"],
                }
            )
        component_rows.sort(key=lambda row: (row["ordinal"], row["material_id"]))
        if ordinals and sorted(ordinals) != list(range(len(ordinals))):
            issues.add("composition:component-ordinals")
        element_rows: list[dict[str, Any]] = []
        for relation in elements:
            if relation["node_kind"] != "element":
                issues.add("composition:element-target")
                continue
            element_rows.append(
                {
                    "element_id": relation["node_id"],
                    "relationship_id": relation["relationship_id"],
                    "attributes": relation["node_attributes"],
                }
            )
        element_rows.sort(key=lambda row: row["element_id"])
        if len(element_rows) > 1:
            issues.add("composition:multiple-elements")
        if component_rows and element_rows:
            basis = "elemental_composite"
        elif component_rows:
            basis = "composite"
        elif element_rows:
            basis = "elemental"
        else:
            basis = "unspecified"
        return {
            "basis": basis,
            "chemical_formula": attributes.get("chemical_formula"),
            "components_initialized": attributes.get("components_initialized"),
            "components": component_rows,
            "elements": element_rows,
        }

    @staticmethod
    def _realized_form_lens(
        forms: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        counts = {kind: 0 for kind in REALIZED_FORM_KINDS}
        for relation in forms:
            node_kind = relation["node_kind"]
            form_kind = node_kind if node_kind in {"fluid", "item_variant"} else "other"
            counts[form_kind] += 1
            attributes = relation["relationship_attributes"]
            rows.append(
                {
                    "form_kind": form_kind,
                    "node_kind": node_kind,
                    "form_node_id": relation["node_id"],
                    "relationship_id": relation["relationship_id"],
                    "relationship_adapter": relation["relationship_adapter"],
                    "prefix_name": (
                        attributes.get("prefix_name")
                        if isinstance(attributes, dict)
                        else None
                    ),
                    "attributes": attributes,
                }
            )
        rows.sort(
            key=lambda row: (
                row["form_kind"],
                row["form_node_id"],
                row["relationship_id"],
            )
        )
        return {
            "count": len(rows),
            "counts_by_kind": counts,
            "forms": rows,
        }

    @staticmethod
    def _generation_lens(
        decisions: list[dict[str, Any]],
        realized_forms: Mapping[str, Any],
    ) -> dict[str, Any]:
        variants_by_prefix: dict[str, list[str]] = {}
        for form in realized_forms["forms"]:
            prefix = form.get("prefix_name")
            if form["form_kind"] == "item_variant" and isinstance(prefix, str):
                variants_by_prefix.setdefault(prefix, []).append(
                    form["form_node_id"]
                )
        rows: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        for decision in decisions:
            realized = sorted(
                set(variants_by_prefix.get(decision["prefix_name"], ()))
            )
            eligible = decision["do_generate_item"]
            if eligible and realized:
                status = "eligible_with_realized_form"
            elif eligible:
                status = "eligible_without_realized_form"
            elif realized:
                status = "ineligible_with_unified_form"
            else:
                status = "ineligible_without_realized_form"
            status_counts[status] = status_counts.get(status, 0) + 1
            rows.append(
                {
                    **decision,
                    "realization_status": status,
                    "realized_item_variant_ids": realized,
                }
            )
        rows.sort(
            key=lambda row: (
                row["prefix_name"].encode("utf-8"),
                row["constraint_node_id"].encode("utf-8"),
            )
        )
        return {
            "decision_count": len(rows),
            "status_counts": {
                key: status_counts[key]
                for key in sorted(status_counts, key=lambda value: value.encode("utf-8"))
            },
            "decisions": rows,
        }

    def _material_row(
        self,
        source: dict[str, Any],
        identity_keys: dict[str, list[str]],
        property_relations: list[dict[str, Any]],
        component_relations: list[dict[str, Any]],
        element_relations: list[dict[str, Any]],
        form_relations: list[dict[str, Any]],
        generation_decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        material_id = source["material_id"]
        attributes = source["node"]["attributes"]
        marker = self._require_attribute(
            attributes, "marker", bool, material_id
        )
        registry_name = self._require_attribute(
            attributes, "registry_name", str, material_id
        )
        namespace = self._require_attribute(
            attributes, "namespace", str, material_id
        )
        name = self._require_attribute(attributes, "name", str, material_id)
        numeric_id = self._require_attribute(attributes, "id", int, material_id)
        component_count = self._require_attribute(
            attributes, "component_count", int, material_id
        )
        property_count = self._require_attribute(
            attributes, "property_count", int, material_id
        )
        fluid_form_count = self._require_attribute(
            attributes, "fluid_form_count", int, material_id
        )
        has_fluid = self._require_attribute(
            attributes, "has_fluid", bool, material_id
        )
        flags = attributes.get("flags")
        if not isinstance(flags, list):
            raise RuntimeGraphQueryError(
                f"material flags are invalid for {material_id}"
            )

        issues: set[str] = set()
        registry_role = "marker" if marker else "persistent"
        resource_keys = identity_keys["material-resource-location"]
        numeric_keys = identity_keys["material-numeric-id"]
        if resource_keys != [registry_name]:
            issues.add("identity:resource-location")
        if registry_role == "persistent" and numeric_keys != [str(numeric_id)]:
            issues.add("identity:numeric-id")
        expected_prefix = namespace + ":"
        if not registry_name.startswith(expected_prefix):
            issues.add("identity:namespace")
        storage_mod = attributes.get("storage_registry_mod_id")
        storage_network = attributes.get("storage_registry_network_id")
        if registry_role == "persistent":
            if (
                not isinstance(storage_mod, str)
                or not storage_mod
                or not isinstance(storage_network, int)
                or isinstance(storage_network, bool)
                or storage_network < 0
            ):
                issues.add("identity:storage-registry")
        elif storage_mod is not None or storage_network is not None:
            issues.add("identity:marker-storage-registry")

        properties = self._property_lens(
            material_id,
            registry_name,
            property_relations,
            issues,
        )
        property_keys = set(properties["keys"])
        issues.update(
            self._property_closure_issues(registry_role, property_keys)
        )
        if property_count != len(properties["verified_values"]):
            issues.add("property:count")
        if has_fluid != ("fluid" in property_keys):
            issues.add("property:fluid-capability")

        composition = self._composition_lens(
            attributes,
            component_relations,
            element_relations,
            issues,
        )
        if component_count != len(composition["components"]):
            issues.add("composition:component-count")

        realized_forms = self._realized_form_lens(form_relations)
        observed_gt_fluid_forms = sum(
            form["form_kind"] == "fluid"
            and form["relationship_adapter"] in self.policy.admitted_material_adapters
            for form in realized_forms["forms"]
        )
        if fluid_form_count != observed_gt_fluid_forms:
            issues.add("form:fluid-count")
        if ("fluid" in property_keys) != bool(observed_gt_fluid_forms):
            issues.add("form:fluid-realization")

        flag_lens = self._flag_lens(flags, property_keys, issues)
        core = {
            "registry_role": registry_role,
            "identity": {
                "registry_name": registry_name,
                "namespace": namespace,
                "name": name,
                "numeric_id": numeric_id,
                "numeric_id_authoritative": registry_role == "persistent",
                "storage_registry": (
                    {
                        "mod_id": storage_mod,
                        "network_id": storage_network,
                    }
                    if registry_role == "persistent"
                    else None
                ),
                "observed_keys": identity_keys,
            },
            "composition": composition,
            "properties": properties,
            "flags": flag_lens,
            "presentation": {
                "rgb": attributes.get("rgb"),
                "icon_set": attributes.get("icon_set"),
            },
            "gregtech_derived_values": {
                "blast_temperature": attributes.get("blast_temperature"),
                "mass": attributes.get("mass"),
                "neutrons": attributes.get("neutrons"),
                "protons": attributes.get("protons"),
                "radioactive": attributes.get("radioactive"),
                "has_fluid_api_value": has_fluid,
                "is_solid_api_value": attributes.get("solid"),
            },
        }
        generation = self._generation_lens(
            generation_decisions,
            realized_forms,
        )
        sorted_issues = sorted(issues, key=lambda value: value.encode("utf-8"))
        return {
            "material_id": material_id,
            "source_adapter": source["adapter"],
            "classification_status": (
                "exact" if not sorted_issues else "frontier"
            ),
            "frontier_issue_codes": sorted_issues,
            "core_sha256": hashlib.sha256(_canonical_bytes(core)).hexdigest(),
            "core": core,
            "form_lens": {
                "prefix_generation": generation,
                "realized": realized_forms,
            },
        }

    @staticmethod
    def _apply_identity_collisions(rows: list[dict[str, Any]]) -> None:
        by_registry_name: dict[str, list[dict[str, Any]]] = {}
        by_storage_id: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        for row in rows:
            identity = row["core"]["identity"]
            by_registry_name.setdefault(identity["registry_name"], []).append(row)
            storage = identity["storage_registry"]
            if storage is not None:
                key = (
                    storage["mod_id"],
                    storage["network_id"],
                    identity["numeric_id"],
                )
                by_storage_id.setdefault(key, []).append(row)
        for group, code in (
            (by_registry_name, "identity:duplicate-registry-name"),
            (by_storage_id, "identity:duplicate-storage-numeric-id"),
        ):
            for matching in group.values():
                if len(matching) < 2:
                    continue
                for row in matching:
                    issues = set(row["frontier_issue_codes"])
                    issues.add(code)
                    row["frontier_issue_codes"] = sorted(issues)
                    row["classification_status"] = "frontier"

    @staticmethod
    def _summary(rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        role_counts = {role: 0 for role in MATERIAL_REGISTRY_ROLES}
        basis_counts = {basis: 0 for basis in COMPOSITION_BASES}
        status_counts = {status: 0 for status in CLASSIFICATION_STATUSES}
        property_counts: dict[str, int] = {}
        issue_counts: dict[str, int] = {}
        form_counts = {kind: 0 for kind in REALIZED_FORM_KINDS}
        for row in rows:
            core = row["core"]
            role_counts[core["registry_role"]] += 1
            basis_counts[core["composition"]["basis"]] += 1
            status_counts[row["classification_status"]] += 1
            for key in core["properties"]["keys"]:
                property_counts[key] = property_counts.get(key, 0) + 1
            for issue in row["frontier_issue_codes"]:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
            for kind, count in row["form_lens"]["realized"]["counts_by_kind"].items():
                form_counts[kind] += count
        return {
            "registry_role_counts": role_counts,
            "composition_basis_counts": basis_counts,
            "classification_status_counts": status_counts,
            "property_material_counts": {
                key: property_counts[key]
                for key in sorted(property_counts, key=lambda value: value.encode("utf-8"))
            },
            "realized_form_counts": form_counts,
            "material_without_realized_form_count": sum(
                row["form_lens"]["realized"]["count"] == 0 for row in rows
            ),
            "frontier_material_count": status_counts["frontier"],
            "frontier_issue_counts": {
                key: issue_counts[key]
                for key in sorted(issue_counts, key=lambda value: value.encode("utf-8"))
            },
        }

    def build(self, scope: ProfileScope) -> MaterialClassificationResult:
        if not isinstance(scope, ProfileScope):
            raise RuntimeGraphQueryError(
                "material classification requires one explicit ProfileScope"
            )
        sources = self._material_nodes(scope)
        material_ids = tuple(source["material_id"] for source in sources)
        identity_keys = self._identity_keys(material_ids)
        properties = self._outgoing_lens(
            scope,
            material_ids,
            "has_property",
            "max_properties_per_material",
        )
        components = self._outgoing_lens(
            scope,
            material_ids,
            "has_component",
            "max_components_per_material",
        )
        elements = self._outgoing_lens(
            scope,
            material_ids,
            "has_element",
            "max_elements_per_material",
        )
        forms = self._outgoing_lens(
            scope,
            material_ids,
            "has_form",
            "max_realized_forms_per_material",
        )
        decisions = self._generation_decisions(scope, material_ids)
        rows = [
            self._material_row(
                source,
                identity_keys[source["material_id"]],
                properties[source["material_id"]],
                components[source["material_id"]],
                elements[source["material_id"]],
                forms[source["material_id"]],
                decisions[source["material_id"]],
            )
            for source in sources
        ]
        self._apply_identity_collisions(rows)
        result_rows = tuple(rows)
        return MaterialClassificationResult(
            scope=scope,
            policy=self.policy,
            bounds=self.bounds,
            rows=result_rows,
            summary=self._summary(result_rows),
        )


def classify_materials(
    reader: RuntimeGraphReader,
    scope: ProfileScope,
    policy: MaterialClassificationPolicy | LegacyMaterialPolicySnapshot,
    bounds: MaterialClassificationBounds = DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS,
) -> MaterialClassificationResult:
    """Classify every material independently inside one exact graph scope."""

    return RuntimeGraphMaterialClassification(reader, policy, bounds).build(scope)


__all__ = [
    "CLASSIFICATION_STATUSES",
    "COMPOSITION_BASES",
    "DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS",
    "MATERIAL_CLASSIFICATION_FORMAT",
    "MATERIAL_IDENTITY_KEY_KINDS",
    "MATERIAL_REGISTRY_ROLES",
    "MaterialClassificationBounds",
    "MaterialClassificationPolicy",
    "MaterialClassificationResult",
    "RuntimeGraphMaterialClassification",
    "classify_materials",
]
