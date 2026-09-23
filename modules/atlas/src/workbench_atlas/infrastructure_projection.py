#!/usr/bin/env python3

"""Normalize exact infrastructure candidates under one fail-closed policy."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from typing import Any

from workbench_atlas.runtime_graph import canonical_json_payload


POLICY_ID = "SUSY-INFRASTRUCTURE-ADMISSION-0001"
VOLTAGE_RULE_ID = "SUSY-GTCEU-VOLTAGE-TIER-0001"
PROJECTION_FORMAT = "susy-infrastructure-requirements-v1"

VOLTAGE_TIERS = (
    (0, "ULV", "Ultra Low Voltage", 8, 7),
    (1, "LV", "Low Voltage", 32, 30),
    (2, "MV", "Medium Voltage", 128, 120),
    (3, "HV", "High Voltage", 512, 480),
    (4, "EV", "Extreme Voltage", 2048, 1920),
    (5, "IV", "Insane Voltage", 8192, 7680),
    (6, "LuV", "Ludicrous Voltage", 32768, 30720),
    (7, "ZPM", "ZPM Voltage", 131072, 122880),
    (8, "UV", "Ultimate Voltage", 524288, 491520),
    (9, "UHV", "Ultra High Voltage", 2097152, 1966080),
    (10, "UEV", "Ultra Excessive Voltage", 8388608, 7864320),
    (11, "UIV", "Ultra Immense Voltage", 33554432, 31457280),
    (12, "UXV", "Ultra Extreme Voltage", 134217728, 125829120),
    (13, "OpV", "Overpowered Voltage", 536870912, 503316480),
    (14, "MAX", "Maximum Voltage", 2147483647, 2013265920),
)

EXCLUDED_CONSTRAINT_KINDS = {
    "effective_method_owners": "developer-context-not-player-prerequisite",
    "mapped_recipe_configuration": "execution-envelope-not-fixed-player-state",
    "output_fit_trimming_and_voiding": "output-behavior-not-infrastructure",
}
INVENTORY_NODE_KINDS = {
    "block",
    "fluid",
    "fluid_variant",
    "ingredient",
    "item",
    "item_variant",
    "ore_dictionary_key",
}


class InfrastructureProjectionError(ValueError):
    """Raised when candidates or normalized semantics are not exact."""


def _canonical_bytes(value: Any) -> bytes:
    return canonical_json_payload(value)


def _required_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InfrastructureProjectionError(f"{label} must be an object")
    return value


def _required_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise InfrastructureProjectionError(f"{label} must be a list")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise InfrastructureProjectionError(f"{label} must be non-empty text")
    return value


def _attributes(record: dict[str, Any]) -> dict[str, Any]:
    return _required_object(record.get("attributes"), "runtime attributes")


def voltage_tier(voltage: int) -> dict[str, Any]:
    """Apply pinned GTUtility.getTierByVoltage semantics to a nonnegative rate."""

    if not isinstance(voltage, int) or isinstance(voltage, bool) or voltage < 0:
        raise InfrastructureProjectionError(
            "voltage tier input must be a nonnegative integer"
        )
    for index, short_name, long_name, nominal, adjusted in VOLTAGE_TIERS:
        if voltage <= nominal:
            return {
                "tier": index,
                "short_name": short_name,
                "long_name": long_name,
                "nominal_voltage": nominal,
                "adjusted_recipe_voltage": adjusted,
            }
    index, short_name, long_name, nominal, adjusted = VOLTAGE_TIERS[-1]
    return {
        "tier": index,
        "short_name": short_name,
        "long_name": long_name,
        "nominal_voltage": nominal,
        "adjusted_recipe_voltage": adjusted,
    }


class _AuthorityIndex:
    def __init__(self, registry: dict[str, Any]) -> None:
        self.registry = registry
        self.citations = {
            _required_text(row.get("id"), "citation id"): row
            for row in _required_list(registry.get("citations"), "citations")
            if isinstance(row, dict)
        }
        self.sets = {
            _required_text(row.get("semantic_kind"), "authority semantic kind"): row
            for row in _required_list(
                registry.get("authority_sets"),
                "authority sets",
            )
            if isinstance(row, dict)
        }
        if len(self.citations) != len(registry["citations"]):
            raise InfrastructureProjectionError(
                "source citation identifiers are duplicated"
            )
        if len(self.sets) != len(registry["authority_sets"]):
            raise InfrastructureProjectionError(
                "source authority semantic kinds are duplicated"
            )

    def authority(self, semantic_kind: str) -> dict[str, Any]:
        result = self.sets.get(semantic_kind)
        if result is None:
            raise InfrastructureProjectionError(
                f"missing source authority set: {semantic_kind}"
            )
        return result

    def citation_ids(self, semantic_kind: str) -> list[str]:
        return list(self.authority(semantic_kind)["citation_ids"])

    def controller_citation(self, factory_owner: str) -> str | None:
        symbol = factory_owner + ".createStructurePattern"
        matches = [
            identifier
            for identifier, row in self.citations.items()
            if row.get("symbol") == symbol
            or (
                isinstance(row.get("symbol"), str)
                and row["symbol"].startswith(factory_owner + ".")
                and "createStructurePattern" in row["symbol"]
            )
        ]
        if len(matches) > 1:
            raise InfrastructureProjectionError(
                f"controller source citation is ambiguous: {factory_owner}"
            )
        return matches[0] if matches else None


def _definition_id(
    kind: str,
    status: str,
    applicability: str,
    subject_id: str,
    relationship_id: str,
    authority_set_ids: list[str],
    citation_ids: list[str],
    derivation_rule_id: str | None,
    facts: dict[str, Any],
) -> str:
    digest = hashlib.sha256(
        _canonical_bytes(
            {
                "policy_id": POLICY_ID,
                "kind": kind,
                "status": status,
                "applicability": applicability,
                "subject_id": subject_id,
                "relationship_id": relationship_id,
                "source_authority_set_ids": sorted(
                    set(authority_set_ids)
                ),
                "citation_ids": sorted(set(citation_ids)),
                "derivation_rule_id": derivation_rule_id,
                "facts": facts,
            }
        )
    ).hexdigest()
    return "INFRA-" + digest[:32].upper()


def _definition(
    *,
    kind: str,
    status: str,
    applicability: str,
    subject_id: str,
    relationship_id: str,
    record_ids: list[str],
    authority_set_ids: list[str],
    citation_ids: list[str],
    derivation_rule_id: str | None,
    facts: dict[str, Any],
) -> dict[str, Any]:
    if status not in {"resolved", "partial", "unresolved"}:
        raise InfrastructureProjectionError(
            f"invalid infrastructure definition status: {status}"
        )
    if applicability not in {
        "always",
        "conditional",
        "alternative",
        "not-applicable",
        "unresolved",
    }:
        raise InfrastructureProjectionError(
            f"invalid infrastructure applicability: {applicability}"
        )
    result = {
        "id": _definition_id(
            kind,
            status,
            applicability,
            subject_id,
            relationship_id,
            authority_set_ids,
            citation_ids,
            derivation_rule_id,
            facts,
        ),
        "kind": kind,
        "status": status,
        "applicability": applicability,
        "subject_id": subject_id,
        "runtime_relationship_ids": [relationship_id],
        "runtime_record_ids": sorted(set(record_ids)),
        "source_authority_set_ids": sorted(set(authority_set_ids)),
        "citation_ids": sorted(set(citation_ids)),
        "derivation_rule_id": derivation_rule_id,
        "facts": facts,
    }
    return result


def _predicate_occurrences(attributes: dict[str, Any]) -> dict[str, dict[str, int]]:
    grid = _required_list(attributes.get("grid"), "multiblock pattern grid")
    repetitions = _required_list(
        attributes.get("aisle_repetitions"),
        "multiblock aisle repetitions",
    )
    if len(grid) != len(repetitions):
        raise InfrastructureProjectionError(
            "multiblock grid depth differs from aisle repetitions"
        )
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"minimum": 0, "maximum": 0}
    )
    for aisle, repetition in zip(grid, repetitions):
        rows = _required_list(aisle, "multiblock aisle")
        counts: Counter[str] = Counter()
        for row in rows:
            for predicate_id in _required_list(row, "multiblock row"):
                counts[_required_text(predicate_id, "predicate id")] += 1
        repeat = _required_object(repetition, "aisle repetition")
        minimum = repeat.get("minimum")
        maximum = repeat.get("maximum")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum < 0
            or maximum < minimum
        ):
            raise InfrastructureProjectionError(
                "multiblock aisle repetition is invalid"
            )
        for predicate_id, count in counts.items():
            totals[predicate_id]["minimum"] += count * minimum
            totals[predicate_id]["maximum"] += count * maximum
    return dict(totals)


def _pattern_facts(attributes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if (
        attributes.get("factory_samples") != 2
        or attributes.get("placed_structure_state_observed") is not False
        or attributes.get("world_pattern_check_invoked") is not False
        or attributes.get("structure_formation_invoked") is not False
    ):
        raise InfrastructureProjectionError(
            "multiblock pattern provenance is not the exact world-free contract"
        )
    occurrences = _predicate_occurrences(attributes)
    predicates = []
    gaps: list[str] = []
    observed_ids: set[str] = set()
    for predicate in _required_list(
        attributes.get("predicates"),
        "multiblock predicates",
    ):
        row = _required_object(predicate, "multiblock predicate")
        predicate_id = _required_text(row.get("predicate_id"), "predicate id")
        if predicate_id in observed_ids or predicate_id not in occurrences:
            raise InfrastructureProjectionError(
                f"multiblock predicate identity is duplicated or unused: {predicate_id}"
            )
        observed_ids.add(predicate_id)
        identity = row.get("traceability_identity", "capture-v1-unspecified")
        alternatives: list[dict[str, Any]] = []
        for list_kind in ("common", "limited"):
            for alternative in _required_list(
                row.get(list_kind),
                f"multiblock predicate {list_kind}",
            ):
                alternative_row = _required_object(
                    alternative,
                    "multiblock simple predicate",
                )
                capture = _required_object(
                    alternative_row.get("predicate_captures"),
                    "multiblock predicate captures",
                )
                predicate_identity = alternative_row.get(
                    "predicate_identity",
                    "capture-v1-unspecified",
                )
                if predicate_identity not in {
                    "capture-v1-unspecified",
                    "constructed",
                    "gt_air",
                    "gt_any",
                }:
                    raise InfrastructureProjectionError(
                        "multiblock simple predicate identity is invalid: "
                        + str(predicate_identity)
                    )
                capture_status = capture.get("status")
                if capture_status == "partial_unrecognized_captures":
                    gaps.append(
                        f"{predicate_id}:{list_kind}:partial-unrecognized-captures"
                    )
                elif (
                    capture_status == "no_captured_fields"
                    and identity not in {"gt_any", "gt_air"}
                    and predicate_identity not in {"gt_any", "gt_air"}
                ):
                    gaps.append(
                        f"{predicate_id}:{list_kind}:predicate-semantics-not-identified"
                    )
                alternatives.append(
                    {
                        "list_kind": list_kind,
                        "alternative_index": alternative_row.get(
                            "alternative_index"
                        ),
                        "minimum_global_count": alternative_row.get(
                            "minimum_global_count"
                        ),
                        "maximum_global_count": alternative_row.get(
                            "maximum_global_count"
                        ),
                        "minimum_layer_count": alternative_row.get(
                            "minimum_layer_count"
                        ),
                        "maximum_layer_count": alternative_row.get(
                            "maximum_layer_count"
                        ),
                        "preview_count": alternative_row.get("preview_count"),
                        "predicate_identity": predicate_identity,
                        "predicate_captures": capture,
                        "preview_candidates": alternative_row.get(
                            "preview_candidates"
                        ),
                    }
                )
        predicates.append(
            {
                "predicate_id": predicate_id,
                "traceability_identity": identity,
                "is_center": row.get("is_center"),
                "is_single": row.get("is_single"),
                "has_air_alternative": row.get("has_air_alternative"),
                "occurrences": occurrences[predicate_id],
                "alternatives": alternatives,
            }
        )
    if set(occurrences) != observed_ids:
        raise InfrastructureProjectionError(
            "multiblock grid references undefined predicates"
        )
    return (
        {
            "machine": attributes.get("machine"),
            "factory_method_owner": attributes.get("factory_method_owner"),
            "factory_method": attributes.get("factory_method"),
            "factory_samples": attributes.get("factory_samples"),
            "pattern_sha256": attributes.get("pattern_sha256"),
            "dimensions": attributes.get("dimensions"),
            "structure_directions": attributes.get("structure_directions"),
            "aisle_repetitions": attributes.get("aisle_repetitions"),
            "grid": attributes.get("grid"),
            "predicates": predicates,
            "placed_structure_state_observed": False,
            "world_pattern_check_invoked": False,
            "structure_formation_invoked": False,
            "predicate_semantic_gaps": sorted(set(gaps)),
        },
        sorted(set(gaps)),
    )


def _recipe_cleanroom_property(operation: dict[str, Any]) -> dict[str, Any] | None:
    producer = _required_object(operation.get("producer"), "operation producer")
    properties = _attributes(producer).get("properties", [])
    if not isinstance(properties, list):
        raise InfrastructureProjectionError("recipe properties must be a list")
    matches = [
        row
        for row in properties
        if isinstance(row, dict) and row.get("key") == "cleanroom"
    ]
    if len(matches) > 1:
        raise InfrastructureProjectionError(
            "recipe contains duplicate cleanroom properties"
        )
    return matches[0] if matches else None


def _single_constraint(
    paths: list[Any],
    constraint_kind: str,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for path in paths:
        if not isinstance(path, dict) or path.get("path_kind") != "direct":
            continue
        relationship = path.get("relationship")
        condition = path.get("condition")
        if (
            not isinstance(relationship, dict)
            or relationship.get("predicate") != "has_constraint"
            or not isinstance(condition, dict)
        ):
            continue
        attributes = condition.get("attributes")
        if (
            isinstance(attributes, dict)
            and attributes.get("constraint_kind") == constraint_kind
        ):
            matches.append(condition)
    if len(matches) > 1:
        raise InfrastructureProjectionError(
            "subject has duplicate infrastructure context constraints: "
            + constraint_kind
        )
    return matches[0] if matches else None


def normalize_infrastructure_candidates(
    candidates: dict[str, Any],
    authority_registry: dict[str, Any],
) -> dict[str, Any]:
    """Apply typed admission while retaining every candidate disposition."""

    if candidates.get("status") != "collected":
        raise InfrastructureProjectionError(
            "infrastructure normalization requires collected candidates"
        )
    authorities = _AuthorityIndex(authority_registry)
    voltage_authority = authorities.authority("voltage-tier")
    voltage_citations = authorities.citation_ids("voltage-tier")
    structure_authority = authorities.authority("multiblock-structure")
    structure_common_citations = {
        "CIT-GTCEU-BLOCK-PATTERN-CHECK",
        "CIT-GTCEU-FACTORY-BLOCK-PATTERN",
        "CIT-GTCEU-MULTIBLOCK-ABILITY",
        "CIT-GTCEU-MULTIBLOCK-CONTROLLER-FORMATION",
        "CIT-GTCEU-MULTIBLOCK-PREDICATE-HELPERS",
        "CIT-GTCEU-TRACEABILITY-BASE-PREDICATES",
        "CIT-GTCEU-TRACEABILITY-CARDINALITY",
    }
    if not structure_common_citations.issubset(
        set(structure_authority["citation_ids"])
    ):
        raise InfrastructureProjectionError(
            "multiblock source authority omits required substrate citations"
        )

    definitions: dict[str, dict[str, Any]] = {}
    operation_rows: list[dict[str, Any]] = []
    disposition_outcomes: dict[
        tuple[str, str],
        dict[tuple[str, str], dict[str, Any]],
    ] = defaultdict(dict)
    unresolved_rows: dict[str, dict[str, Any]] = {}

    def retain_definition(row: dict[str, Any]) -> str:
        previous = definitions.get(row["id"])
        if previous is not None and previous != row:
            previous_identity = {
                key: value
                for key, value in previous.items()
                if key != "runtime_record_ids"
            }
            row_identity = {
                key: value
                for key, value in row.items()
                if key != "runtime_record_ids"
            }
            if previous_identity != row_identity:
                raise InfrastructureProjectionError(
                    "infrastructure definition identity collision: "
                    + row["id"]
                )
            row = {
                **previous,
                "runtime_record_ids": sorted(
                    set(previous["runtime_record_ids"])
                    | set(row["runtime_record_ids"])
                ),
            }
        definitions[row["id"]] = row
        if row["status"] != "resolved":
            unresolved_rows[row["id"]] = {
                "id": row["id"],
                "code": (
                    "partial-requirement-semantics"
                    if row["status"] == "partial"
                    else "unresolved-requirement-semantics"
                ),
                "subject_id": row["subject_id"],
                "runtime_relationship_id": row["runtime_relationship_ids"][0],
                "detail": row["facts"].get(
                    "unresolved_reason",
                    "requirement is not fully source- and runtime-closed",
                ),
            }
        return row["id"]

    def record_outcome(
        relationship: dict[str, Any],
        operation_key: str,
        disposition: str,
        reason: str,
        definition_ids: list[str],
    ) -> None:
        relationship_id = _required_text(
            relationship.get("id"),
            "candidate relationship id",
        )
        predicate = _required_text(
            relationship.get("predicate"),
            "candidate predicate",
        )
        key = (relationship_id, predicate)
        outcome_key = (disposition, reason)
        outcome = disposition_outcomes[key].get(outcome_key)
        if outcome is None:
            outcome = {
                "disposition": disposition,
                "reason": reason,
                "occurrence_count": 0,
                "operation_keys": set(),
                "definition_ids": set(),
            }
            disposition_outcomes[key][outcome_key] = outcome
        outcome["occurrence_count"] += 1
        outcome["operation_keys"].add(operation_key)
        outcome["definition_ids"].update(definition_ids)

    for candidate_operation in _required_list(
        candidates.get("operations"),
        "candidate operations",
    ):
        context = _required_object(
            candidate_operation,
            "candidate operation",
        )
        operation = _required_object(context.get("operation"), "operation")
        origin = _required_text(context.get("origin"), "operation origin")
        route_id = _required_text(operation.get("id"), "operation route id")
        subproblem_id = _required_text(
            operation.get("subproblem_id"),
            "operation subproblem id",
        )
        operation_key = "|".join((origin, subproblem_id, route_id))
        requirement_ids: set[str] = set()
        unresolved_ids: set[str] = set()
        subject_ids: set[str] = set()
        for subject_context in _required_list(
            context.get("condition_subjects"),
            "condition subjects",
        ):
            subject_row = _required_object(
                subject_context,
                "condition subject",
            )
            subject = _required_object(subject_row.get("record"), "subject")
            subject_id = _required_text(subject.get("id"), "subject id")
            subject_ids.add(subject_id)
            candidate_paths = _required_list(
                subject_row.get("candidate_paths"),
                "candidate paths",
            )
            effective_methods = _single_constraint(
                candidate_paths,
                "effective_method_owners",
            )
            effective_overclock_owner = None
            if effective_methods is not None:
                logic_methods = _attributes(effective_methods).get(
                    "logic_methods"
                )
                if isinstance(logic_methods, dict):
                    effective_overclock_owner = logic_methods.get(
                        "calculateOverclock"
                    )
            for path in candidate_paths:
                path_row = _required_object(path, "candidate path")
                relationship = _required_object(
                    path_row.get("relationship"),
                    "candidate relationship",
                )
                relationship_id = _required_text(
                    relationship.get("id"),
                    "candidate relationship id",
                )
                predicate = _required_text(
                    relationship.get("predicate"),
                    "candidate predicate",
                )
                definition_rows: list[dict[str, Any]] = []
                disposition = "excluded"
                reason = "not-an-infrastructure-prerequisite"

                if path_row.get("path_kind") == "slot":
                    alternatives = _required_list(
                        path_row.get("alternatives"),
                        "slot alternatives",
                    )
                    kinds = {
                        _required_object(row, "slot alternative")
                        .get("node", {})
                        .get("kind")
                        for row in alternatives
                        if isinstance(row, dict)
                    }
                    if predicate == "requires" and kinds.issubset(
                        INVENTORY_NODE_KINDS
                    ):
                        reason = (
                            "reusable-inventory-requirement-owned-by-prerequisites"
                        )
                    else:
                        disposition = "unresolved"
                        reason = "slot-condition-kind-has-no-admission-policy"
                else:
                    condition = _required_object(
                        path_row.get("condition"),
                        "direct condition",
                    )
                    condition_id = _required_text(
                        condition.get("id"),
                        "condition id",
                    )
                    condition_attributes = _attributes(condition)
                    if predicate == "uses_energy":
                        producer = _required_object(
                            operation.get("producer"),
                            "operation producer",
                        )
                        producer_attributes = _attributes(producer)
                        eut = producer_attributes.get("eut")
                        carrier = condition_attributes.get(
                            "carrier",
                            condition_attributes.get("name"),
                        )
                        if (
                            isinstance(eut, int)
                            and not isinstance(eut, bool)
                            and eut >= 0
                            and carrier == "gregtech:eu"
                            and effective_overclock_owner
                            == "gregtech.api.capability.impl.AbstractRecipeLogic"
                        ):
                            tier = voltage_tier(eut)
                            machine_tier = _attributes(subject).get("tier")
                            facts = {
                                "carrier": carrier,
                                "recipe_eut": eut,
                                "effective_calculate_overclock_owner": (
                                    effective_overclock_owner
                                ),
                                "minimum_recipe_tier": tier,
                                "selected_machine_registered_tier": machine_tier,
                                "selected_machine_registered_tier_name": (
                                    VOLTAGE_TIERS[machine_tier][1]
                                    if isinstance(machine_tier, int)
                                    and not isinstance(machine_tier, bool)
                                    and 0 <= machine_tier < len(VOLTAGE_TIERS)
                                    else None
                                ),
                                "capacity_status": (
                                    "registered-prototype-satisfies"
                                    if isinstance(machine_tier, int)
                                    and not isinstance(machine_tier, bool)
                                    and machine_tier >= tier["tier"]
                                    else "formed-energy-input-unresolved"
                                    if machine_tier is None
                                    else "registered-prototype-below-recipe-tier"
                                ),
                            }
                            definition_rows.append(
                                _definition(
                                    kind="energy-and-voltage",
                                    status=(
                                        "partial"
                                        if facts["capacity_status"]
                                        == "formed-energy-input-unresolved"
                                        else "resolved"
                                    ),
                                    applicability="always",
                                    subject_id=subject_id,
                                    relationship_id=relationship_id,
                                    record_ids=[
                                        subject_id,
                                        condition_id,
                                        _required_text(
                                            producer.get("id"),
                                            "producer id",
                                        ),
                                        _required_text(
                                            effective_methods.get("id"),
                                            "effective method constraint id",
                                        ),
                                    ],
                                    authority_set_ids=[
                                        voltage_authority["id"]
                                    ],
                                    citation_ids=voltage_citations,
                                    derivation_rule_id=VOLTAGE_RULE_ID,
                                    facts=facts,
                                )
                            )
                            disposition = "admitted"
                            reason = "source-backed-energy-and-voltage"
                        else:
                            disposition = "unresolved"
                            reason = (
                                "effective-overclock-owner-has-no-source-policy:"
                                + str(effective_overclock_owner)
                                if carrier == "gregtech:eu"
                                and isinstance(eut, int)
                                and not isinstance(eut, bool)
                                and eut >= 0
                                and effective_overclock_owner
                                != "gregtech.api.capability.impl.AbstractRecipeLogic"
                                else
                                "energy-carrier-has-no-voltage-policy:"
                                + str(carrier)
                                if isinstance(carrier, str)
                                and carrier
                                and carrier != "gregtech:eu"
                                else "energy-rate-or-carrier-is-not-finite"
                            )
                    elif predicate == "produces_energy":
                        reason = "energy-output-is-not-an-input-prerequisite"
                    elif predicate == "configured_by":
                        reason = (
                            "configuration-domain-link-is-context-not-requirement"
                        )
                    elif predicate == "defined_by_resource":
                        reason = "resource-definition-link-is-context"
                    elif predicate == "spawns_in":
                        representation = condition_attributes.get(
                            "representation"
                        )
                        source_bound = representation in {
                            "susy_loaded_world_planet",
                            "susy_planetoid_route",
                        }
                        facts = {
                            "dimension": condition,
                            "unresolved_reason": (
                                (
                                    "dimension registration is source-backed, "
                                    "but this relation does not prove current "
                                    "player location"
                                )
                                if source_bound
                                else (
                                    "dimension relation has no exact "
                                    "owner-specific source authority"
                                )
                            ),
                        }
                        dimension_authority = (
                            authorities.authority(
                                "dimension-registration"
                            )
                            if source_bound
                            else None
                        )
                        definition_rows.append(
                            _definition(
                                kind="dimension",
                                status=(
                                    "partial"
                                    if source_bound
                                    else "unresolved"
                                ),
                                applicability=(
                                    "conditional"
                                    if source_bound
                                    else "unresolved"
                                ),
                                subject_id=subject_id,
                                relationship_id=relationship_id,
                                record_ids=[subject_id, condition_id],
                                authority_set_ids=(
                                    [dimension_authority["id"]]
                                    if dimension_authority is not None
                                    else []
                                ),
                                citation_ids=(
                                    list(
                                        dimension_authority[
                                            "citation_ids"
                                        ]
                                    )
                                    if dimension_authority is not None
                                    else []
                                ),
                                derivation_rule_id=None,
                                facts=facts,
                            )
                        )
                        disposition = (
                            "admitted" if source_bound else "unresolved"
                        )
                        reason = (
                            "source-backed-dimension-relation"
                            if source_bound
                            else "missing-dimension-owner-source-citation"
                        )
                    elif predicate == "has_constraint":
                        constraint_kind = condition_attributes.get(
                            "constraint_kind"
                        )
                        if constraint_kind == "multiblock_structure_pattern":
                            pattern_facts, gaps = _pattern_facts(
                                condition_attributes
                            )
                            factory_owner = _required_text(
                                pattern_facts.get("factory_method_owner"),
                                "pattern factory owner",
                            )
                            controller_citation = (
                                authorities.controller_citation(factory_owner)
                            )
                            citations = sorted(
                                structure_common_citations
                                | (
                                    {controller_citation}
                                    if controller_citation is not None
                                    else set()
                                )
                            )
                            source_gap = controller_citation is None
                            if source_gap:
                                pattern_facts["unresolved_reason"] = (
                                    "exact controller createStructurePattern "
                                    "source is not pinned"
                                )
                            elif gaps:
                                pattern_facts["unresolved_reason"] = (
                                    "one or more constructed predicate closures "
                                    "remain only partially interpreted"
                                )
                            definition_rows.append(
                                _definition(
                                    kind="machine-structure",
                                    status=(
                                        "unresolved"
                                        if source_gap
                                        else "partial"
                                        if gaps
                                        else "resolved"
                                    ),
                                    applicability=(
                                        "unresolved" if source_gap else "always"
                                    ),
                                    subject_id=subject_id,
                                    relationship_id=relationship_id,
                                    record_ids=[subject_id, condition_id],
                                    authority_set_ids=[
                                        structure_authority["id"]
                                    ],
                                    citation_ids=citations,
                                    derivation_rule_id=None,
                                    facts=pattern_facts,
                                )
                            )
                            disposition = (
                                "unresolved" if source_gap else "admitted"
                            )
                            reason = (
                                "missing-controller-source-citation"
                                if source_gap
                                else "source-backed-constructed-pattern"
                            )
                        elif constraint_kind == "no_energy_recipe_logic":
                            logic_class = _attributes(subject).get(
                                "recipe_logic_class"
                            )
                            no_energy_semantic_kind = {
                                (
                                    "gregtech.api.capability.impl."
                                    "PrimitiveRecipeLogic"
                                ): "no-energy-logic",
                                (
                                    "supersymmetry.api.capability.impl."
                                    "NoEnergyMultiblockRecipeLogic"
                                ): "susy-no-energy-logic",
                            }.get(logic_class)
                            if no_energy_semantic_kind is None:
                                disposition = "unresolved"
                                reason = (
                                    "no-energy-logic-class-has-no-source-policy:"
                                    + str(logic_class)
                                )
                            else:
                                no_energy_authority = authorities.authority(
                                    no_energy_semantic_kind
                                )
                                facts = {
                                    **condition_attributes,
                                    "recipe_logic_class": logic_class,
                                }
                                definition_rows.append(
                                    _definition(
                                        kind="energy-input",
                                        status="resolved",
                                        applicability="not-applicable",
                                        subject_id=subject_id,
                                        relationship_id=relationship_id,
                                        record_ids=[subject_id, condition_id],
                                        authority_set_ids=[
                                            no_energy_authority["id"]
                                        ],
                                        citation_ids=list(
                                            no_energy_authority["citation_ids"]
                                        ),
                                        derivation_rule_id=None,
                                        facts=facts,
                                    )
                                )
                                disposition = "admitted"
                                reason = "source-backed-no-energy-logic"
                        elif constraint_kind == "implicit_steam_energy_carrier":
                            steam_authority = authorities.authority(
                                "steam-energy"
                            )
                            definition_rows.append(
                                _definition(
                                    kind="steam-energy",
                                    status="resolved",
                                    applicability="always",
                                    subject_id=subject_id,
                                    relationship_id=relationship_id,
                                    record_ids=[subject_id, condition_id],
                                    authority_set_ids=[steam_authority["id"]],
                                    citation_ids=list(
                                        steam_authority["citation_ids"]
                                    ),
                                    derivation_rule_id=None,
                                    facts=dict(condition_attributes),
                                )
                            )
                            disposition = "admitted"
                            reason = "source-backed-steam-energy"
                        elif constraint_kind == "ambient_water_formula":
                            water_authority = authorities.authority(
                                "primitive-water-environment"
                            )
                            definition_rows.append(
                                _definition(
                                    kind="environment",
                                    status="resolved",
                                    applicability="conditional",
                                    subject_id=subject_id,
                                    relationship_id=relationship_id,
                                    record_ids=[subject_id, condition_id],
                                    authority_set_ids=[water_authority["id"]],
                                    citation_ids=list(
                                        water_authority["citation_ids"]
                                    ),
                                    derivation_rule_id=None,
                                    facts=dict(condition_attributes),
                                )
                            )
                            disposition = "admitted"
                            reason = "source-backed-environment-formula"
                        elif constraint_kind == "infinite_water_supply":
                            water_authority = authorities.authority(
                                "infinite-water-supply"
                            )
                            definition_rows.append(
                                _definition(
                                    kind="environment",
                                    status="resolved",
                                    applicability="always",
                                    subject_id=subject_id,
                                    relationship_id=relationship_id,
                                    record_ids=[subject_id, condition_id],
                                    authority_set_ids=[
                                        water_authority["id"]
                                    ],
                                    citation_ids=list(
                                        water_authority["citation_ids"]
                                    ),
                                    derivation_rule_id=None,
                                    facts=dict(condition_attributes),
                                )
                            )
                            disposition = "admitted"
                            reason = "source-backed-infinite-water-supply"
                        elif constraint_kind == "cleanroom_recipe_envelope":
                            cleanroom_property = _recipe_cleanroom_property(
                                operation
                            )
                            if cleanroom_property is None:
                                reason = (
                                    "operation-has-no-cleanroom-recipe-property"
                                )
                            else:
                                cleanroom_authority = authorities.authority(
                                    "cleanroom-configuration"
                                )
                                effective = condition_attributes.get(
                                    "clean_multiblocks_effective"
                                )
                                machine_attributes = _attributes(subject)
                                is_multiblock = (
                                    machine_attributes.get("controller_kind")
                                    == "multiblock_controller"
                                )
                                receiver = condition_attributes.get(
                                    "machine_is_cleanroom_receiver"
                                )
                                if effective is True and is_multiblock:
                                    applicability = "not-applicable"
                                    status = "resolved"
                                    config_result = (
                                        "bypassed-for-multiblock-by-effective-config"
                                    )
                                elif effective is False and receiver is True:
                                    applicability = "conditional"
                                    status = "resolved"
                                    config_result = (
                                        "compatible-cleanroom-provider-required"
                                    )
                                elif effective is False and receiver is False:
                                    applicability = "always"
                                    status = "resolved"
                                    config_result = (
                                        "selected-machine-rejects-cleanroom-recipe"
                                    )
                                else:
                                    applicability = "unresolved"
                                    status = "unresolved"
                                    config_result = (
                                        "effective-clean-multiblocks-value-missing"
                                    )
                                facts = {
                                    "recipe_property": cleanroom_property,
                                    "clean_multiblocks_effective": effective,
                                    "machine_is_multiblock": is_multiblock,
                                    "machine_is_cleanroom_receiver": receiver,
                                    "result": config_result,
                                }
                                if status == "unresolved":
                                    facts["unresolved_reason"] = config_result
                                definition_rows.append(
                                    _definition(
                                        kind="cleanroom-configuration",
                                        status=status,
                                        applicability=applicability,
                                        subject_id=subject_id,
                                        relationship_id=relationship_id,
                                        record_ids=[
                                            subject_id,
                                            condition_id,
                                            _required_text(
                                                operation["producer"].get("id"),
                                                "producer id",
                                            ),
                                        ],
                                        authority_set_ids=[
                                            cleanroom_authority["id"]
                                        ],
                                        citation_ids=list(
                                            cleanroom_authority["citation_ids"]
                                        ),
                                        derivation_rule_id=None,
                                        facts=facts,
                                    )
                                )
                                disposition = (
                                    "admitted"
                                    if status == "resolved"
                                    else "unresolved"
                                )
                                reason = (
                                    "source-backed-cleanroom-configuration"
                                    if status == "resolved"
                                    else config_result
                                )
                        elif constraint_kind == "gt_recipe_property":
                            property_row = condition_attributes.get(
                                "property"
                            )
                            property_key = (
                                property_row.get("key")
                                if isinstance(property_row, dict)
                                else None
                            )
                            if property_key == "cleanroom":
                                reason = (
                                    "recipe-property-is-consumed-by-"
                                    "cleanroom-configuration-policy"
                                )
                            elif property_key == "primitive_property":
                                reason = (
                                    "primitive-recipe-marker-is-context-"
                                    "not-an-independent-requirement"
                                )
                            else:
                                disposition = "unresolved"
                                reason = (
                                    "recipe-property-has-no-"
                                    "infrastructure-admission-policy:"
                                    + str(property_key)
                                )
                        elif constraint_kind in EXCLUDED_CONSTRAINT_KINDS:
                            reason = EXCLUDED_CONSTRAINT_KINDS[
                                str(constraint_kind)
                            ]
                        else:
                            disposition = "unresolved"
                            reason = (
                                "constraint-kind-has-no-admission-policy:"
                                + str(constraint_kind)
                            )
                    else:
                        disposition = "unresolved"
                        reason = (
                            "predicate-has-no-infrastructure-admission-policy:"
                            + predicate
                        )

                definition_ids: list[str] = []
                for definition_row in definition_rows:
                    definition_id = retain_definition(definition_row)
                    definition_ids.append(definition_id)
                    if definition_row["status"] == "resolved":
                        requirement_ids.add(definition_id)
                    else:
                        unresolved_ids.add(definition_id)
                if disposition == "unresolved" and not definition_ids:
                    facts = {
                        "predicate": predicate,
                        "reason": reason,
                        "unresolved_reason": reason,
                    }
                    unresolved_definition = _definition(
                        kind="unresolved-candidate",
                        status="unresolved",
                        applicability="unresolved",
                        subject_id=subject_id,
                        relationship_id=relationship_id,
                        record_ids=[subject_id],
                        authority_set_ids=[],
                        citation_ids=[],
                        derivation_rule_id=None,
                        facts=facts,
                    )
                    unresolved_id = retain_definition(unresolved_definition)
                    definition_ids.append(unresolved_id)
                    unresolved_ids.add(unresolved_id)
                record_outcome(
                    relationship,
                    operation_key,
                    disposition,
                    reason,
                    definition_ids,
                )
        operation_rows.append(
            {
                "operation_key": operation_key,
                "origin": origin,
                "route_id": route_id,
                "subproblem_id": subproblem_id,
                "operation_owner_id": _required_text(
                    operation["producer"].get("id"),
                    "operation owner id",
                ),
                "condition_subject_ids": sorted(subject_ids),
                "requirement_ids": sorted(requirement_ids),
                "unresolved_ids": sorted(unresolved_ids),
            }
        )

    disposition_rows: list[dict[str, Any]] = []
    for (relationship_id, predicate), outcomes in sorted(
        disposition_outcomes.items()
    ):
        disposition_rows.append(
            {
                "relationship_id": relationship_id,
                "predicate": predicate,
                "outcomes": [
                    {
                        "disposition": row["disposition"],
                        "reason": row["reason"],
                        "occurrence_count": row["occurrence_count"],
                        "operation_keys": sorted(row["operation_keys"]),
                        "definition_ids": sorted(row["definition_ids"]),
                    }
                    for row in sorted(
                        outcomes.values(),
                        key=lambda item: (
                            item["disposition"],
                            item["reason"],
                        ),
                    )
                ],
            }
        )

    definition_rows = [definitions[key] for key in sorted(definitions)]
    used_authority_ids = sorted(
        {
            identifier
            for row in definition_rows
            for identifier in row["source_authority_set_ids"]
        }
    )
    used_citation_ids = sorted(
        {
            identifier
            for row in definition_rows
            for identifier in row["citation_ids"]
        }
    )
    disposition_counts = Counter(
        outcome["disposition"]
        for row in disposition_rows
        for outcome in row["outcomes"]
        for _ in range(outcome["occurrence_count"])
    )
    status = (
        "partial"
        if unresolved_rows
        or candidates["source_truncation"]["material_routes"].get("truncated")
        or candidates["source_truncation"]["machine_construction"].get(
            "truncated"
        )
        else "answered"
    )
    return {
        "format": PROJECTION_FORMAT,
        "status": status,
        "policy": {
            "id": POLICY_ID,
            "unknown_kinds": "fail-closed-as-unresolved",
            "absence_semantics": (
                "missing-positive-path-is-not-negative-proof"
            ),
            "voltage_tier_rule_id": VOLTAGE_RULE_ID,
        },
        "assumptions": {
            "placed_structure_state": "not-observed",
            "power_network_capacity": "not-observed",
            "chunk_loading": "not-observed",
            "player_location": "not-observed",
            "completeness": "bounded-retained-operation-domains",
        },
        "definitions": definition_rows,
        "operations": operation_rows,
        "candidate_dispositions": disposition_rows,
        "unresolved": [
            unresolved_rows[key] for key in sorted(unresolved_rows)
        ],
        "summary": {
            "operation_count": len(operation_rows),
            "definition_count": len(definition_rows),
            "resolved_definition_count": sum(
                row["status"] == "resolved" for row in definition_rows
            ),
            "partial_definition_count": sum(
                row["status"] == "partial" for row in definition_rows
            ),
            "unresolved_definition_count": sum(
                row["status"] == "unresolved" for row in definition_rows
            ),
            "candidate_relationship_count": len(disposition_rows),
            "candidate_occurrence_count": sum(disposition_counts.values()),
            "candidate_disposition_counts": {
                key: disposition_counts[key]
                for key in sorted(disposition_counts)
            },
        },
        "source_authority_registry_id": authority_registry.get("registry_id"),
        "used_source_authority_set_ids": used_authority_ids,
        "used_citation_ids": used_citation_ids,
        "source_truncation": candidates["source_truncation"],
        "evidence_ids": [],
    }


def validate_infrastructure_projection(
    projection: dict[str, Any],
    candidates: dict[str, Any],
    authority_registry: dict[str, Any],
) -> None:
    """Recompute the complete typed projection and reject semantic drift."""

    expected = normalize_infrastructure_candidates(
        candidates,
        authority_registry,
    )
    actual = dict(projection)
    actual.pop("candidate_basis", None)
    actual.pop("source_authority_registry", None)
    actual["evidence_ids"] = []
    if actual != expected:
        raise InfrastructureProjectionError(
            "infrastructure projection differs from policy recomputation"
        )
