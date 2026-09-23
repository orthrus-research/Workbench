#!/usr/bin/env python3

"""Tests for fail-closed infrastructure normalization."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import unittest

import workbench_atlas.infrastructure_projection as projection
import workbench_atlas.infrastructure_source_authorities as authorities


def node(identifier: str, kind: str, attributes: dict) -> dict:
    return {
        "record_type": "node",
        "id": identifier,
        "kind": kind,
        "scope": {
            "profile": "COMMON_FINAL_STATE",
            "physical_side": "DEDICATED_SERVER",
            "adapter": "test",
            "snapshot_id": "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0",
        },
        "attributes": attributes,
    }


def edge(
    identifier: str,
    predicate: str,
    subject: str,
    target: str,
) -> dict:
    return {
        "record_type": "edge",
        "id": identifier,
        "predicate": predicate,
        "subject": subject,
        "object": target,
        "scope": {
            "profile": "COMMON_FINAL_STATE",
            "physical_side": "DEDICATED_SERVER",
            "adapter": "test",
            "snapshot_id": "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0",
        },
        "attributes": {},
    }


def direct(
    subject: dict,
    predicate: str,
    condition: dict,
    ordinal: int,
) -> dict:
    return {
        "path_kind": "direct",
        "scope": {
            "profile": "COMMON_FINAL_STATE",
            "physical_side": "DEDICATED_SERVER",
        },
        "subject": subject,
        "relationship": edge(
            f"rg:test:edge:{ordinal}",
            predicate,
            subject["id"],
            condition["id"],
        ),
        "condition": condition,
    }


def effective_overclock_path(
    subject: dict,
    ordinal: int,
    owner: str = "gregtech.api.capability.impl.AbstractRecipeLogic",
) -> dict:
    return direct(
        subject,
        "has_constraint",
        node(
            f"rg:test:constraint:effective-methods:{ordinal}",
            "constraint",
            {
                "constraint_kind": "effective_method_owners",
                "logic_methods": {"calculateOverclock": owner},
            },
        ),
        ordinal,
    )


def pattern_attributes(
    owner: str = (
        "supersymmetry.common.metatileentities.multi.electric."
        "MetaTileEntityBlender"
    ),
) -> dict:
    capture = {
        "allowed_ability_names": [],
        "allowed_block_registry_names": [],
        "allowed_block_states": [],
        "allowed_material_registry_names": [],
        "allowed_meta_tile_entity_ids": [],
        "captured_scalars": [],
        "captured_field_count": 0,
        "capture_object_count": 1,
        "unrecognized_capture_types": [],
        "status": "no_captured_fields",
    }
    alternative = {
        "alternative_index": 0,
        "list_kind": "common",
        "maximum_global_count": -1,
        "maximum_layer_count": -1,
        "minimum_global_count": -1,
        "minimum_layer_count": -1,
        "preview_count": -1,
        "predicate_identity": "gt_any",
        "predicate_captures": capture,
        "preview_candidates": {
            "domain_semantics": (
                "preview_candidates_are_not_assumed_to_exhaust_the_predicate_domain"
            ),
            "status": "not_enumerable",
            "values": [],
        },
    }
    return {
        "constraint_kind": "multiblock_structure_pattern",
        "machine": "susy:blender",
        "factory_method_owner": owner,
        "factory_method": "createStructurePattern()",
        "factory_samples": 2,
        "pattern_sha256": "a" * 64,
        "dimensions": {"x": 1, "y": 1, "z": 2},
        "structure_directions": ["RIGHT", "UP", "FRONT"],
        "aisle_repetitions": [
            {"minimum": 1, "maximum": 1},
            {"minimum": 2, "maximum": 3},
        ],
        "grid": [[["predicate-0000"]], [["predicate-0000"]]],
        "predicates": [
            {
                "predicate_id": "predicate-0000",
                "traceability_identity": "constructed",
                "is_center": True,
                "is_single": True,
                "has_air_alternative": False,
                "common": [alternative],
                "limited": [],
            }
        ],
        "placed_structure_state_observed": False,
        "world_pattern_check_invoked": False,
        "structure_formation_invoked": False,
    }


class InfrastructureProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority_registry = authorities.load_authority_registry()
        self.recipe = node(
            "rg:test:recipe:one",
            "recipe",
            {"eut": 120, "properties": []},
        )
        self.machine = node(
            "rg:test:machine:susy:blender",
            "machine",
            {
                "controller_kind": "multiblock_controller",
                "tier": 2,
            },
        )
        self.energy = node(
            "rg:test:energy:eu",
            "energy_carrier",
            {"carrier": "gregtech:eu"},
        )
        self.pattern = node(
            "rg:test:constraint:pattern",
            "constraint",
            pattern_attributes(),
        )

    def candidates(self, paths: list[dict]) -> dict:
        return {
            "status": "collected",
            "assumptions": {},
            "operations": [
                {
                    "origin": "material-route",
                    "operation": {
                        "id": "route-1",
                        "subproblem_id": "subproblem-1",
                        "producer": self.recipe,
                    },
                    "condition_subjects": [
                        {
                            "role": "execution-machine",
                            "record": self.machine,
                            "candidate_paths": paths,
                        }
                    ],
                }
            ],
            "summary": {},
            "source_truncation": {
                "material_routes": {"truncated": False},
                "machine_construction": {"truncated": False},
            },
        }

    def test_voltage_rule_matches_gtceu_ceiling_semantics(self) -> None:
        self.assertEqual("ULV", projection.voltage_tier(0)["short_name"])
        self.assertEqual("ULV", projection.voltage_tier(8)["short_name"])
        self.assertEqual("LV", projection.voltage_tier(9)["short_name"])
        self.assertEqual("LV", projection.voltage_tier(32)["short_name"])
        self.assertEqual("MV", projection.voltage_tier(120)["short_name"])
        self.assertEqual("MAX", projection.voltage_tier(2**63)["short_name"])
        with self.assertRaises(projection.InfrastructureProjectionError):
            projection.voltage_tier(-1)

    def test_energy_and_cited_pattern_are_typed_and_source_bound(self) -> None:
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(self.machine, "uses_energy", self.energy, 0),
                    direct(self.machine, "has_constraint", self.pattern, 1),
                    effective_overclock_path(self.machine, 2),
                ]
            ),
            self.authority_registry,
        )
        self.assertEqual("answered", result["status"])
        self.assertEqual(2, result["summary"]["resolved_definition_count"])
        by_kind = {row["kind"]: row for row in result["definitions"]}
        self.assertEqual(
            "MV",
            by_kind["energy-and-voltage"]["facts"]["minimum_recipe_tier"][
                "short_name"
            ],
        )
        self.assertEqual(
            {"minimum": 3, "maximum": 4},
            by_kind["machine-structure"]["facts"]["predicates"][0][
                "occurrences"
            ],
        )
        self.assertIn(
            "CIT-SUSYCORE-BLENDER-STRUCTURE",
            by_kind["machine-structure"]["citation_ids"],
        )
        projection.validate_infrastructure_projection(
            result,
            self.candidates(
                [
                    direct(self.machine, "uses_energy", self.energy, 0),
                    direct(self.machine, "has_constraint", self.pattern, 1),
                    effective_overclock_path(self.machine, 2),
                ]
            ),
            self.authority_registry,
        )

    def test_shared_definition_unions_contextual_runtime_provenance(
        self,
    ) -> None:
        candidates = self.candidates(
            [
                direct(self.machine, "uses_energy", self.energy, 20),
                effective_overclock_path(self.machine, 21),
            ]
        )
        second = deepcopy(candidates["operations"][0])
        second["operation"]["id"] = "route-2"
        second["operation"]["subproblem_id"] = "subproblem-2"
        second["operation"]["producer"] = node(
            "rg:test:recipe:two",
            "recipe",
            {"eut": 120, "properties": []},
        )
        candidates["operations"].append(second)

        result = projection.normalize_infrastructure_candidates(
            candidates,
            self.authority_registry,
        )
        self.assertEqual(1, len(result["definitions"]))
        self.assertEqual(
            {
                self.machine["id"],
                self.energy["id"],
                self.recipe["id"],
                "rg:test:recipe:two",
                "rg:test:constraint:effective-methods:21",
            },
            set(result["definitions"][0]["runtime_record_ids"]),
        )
        energy_disposition = next(
            row
            for row in result["candidate_dispositions"]
            if row["predicate"] == "uses_energy"
        )
        self.assertEqual(
            2,
            energy_disposition["outcomes"][0]["occurrence_count"],
        )

    def test_energy_capacity_distinguishes_proven_rejection_from_unknown(
        self,
    ) -> None:
        self.recipe["attributes"]["eut"] = 512
        below = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(self.machine, "uses_energy", self.energy, 21),
                    effective_overclock_path(self.machine, 23),
                ]
            ),
            self.authority_registry,
        )
        definition = below["definitions"][0]
        self.assertEqual("resolved", definition["status"])
        self.assertEqual(
            "registered-prototype-below-recipe-tier",
            definition["facts"]["capacity_status"],
        )

        self.machine["attributes"]["tier"] = None
        unknown = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(self.machine, "uses_energy", self.energy, 22),
                    effective_overclock_path(self.machine, 24),
                ]
            ),
            self.authority_registry,
        )
        definition = unknown["definitions"][0]
        self.assertEqual("partial", definition["status"])
        self.assertEqual(
            "formed-energy-input-unresolved",
            definition["facts"]["capacity_status"],
        )

    def test_uncited_effective_overclock_owner_fails_closed(self) -> None:
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(self.machine, "uses_energy", self.energy, 25),
                    effective_overclock_path(
                        self.machine,
                        26,
                        "example.CustomRecipeLogic",
                    ),
                ]
            ),
            self.authority_registry,
        )
        energy_outcome = next(
            row["outcomes"][0]
            for row in result["candidate_dispositions"]
            if row["predicate"] == "uses_energy"
        )
        self.assertEqual("unresolved", energy_outcome["disposition"])
        self.assertEqual(
            "effective-overclock-owner-has-no-source-policy:"
            "example.CustomRecipeLogic",
            energy_outcome["reason"],
        )

    def test_uncited_controller_fails_closed(self) -> None:
        self.pattern["attributes"] = pattern_attributes(
            "gregicality.multiblocks.example.UnpinnedController"
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [direct(self.machine, "has_constraint", self.pattern, 2)]
            ),
            self.authority_registry,
        )
        definition = result["definitions"][0]
        self.assertEqual("unresolved", definition["status"])
        self.assertEqual("unresolved", definition["applicability"])
        self.assertEqual(
            "missing-controller-source-citation",
            result["candidate_dispositions"][0]["outcomes"][0]["reason"],
        )

    def test_cleanroom_config_is_contextual_to_recipe_property(self) -> None:
        self.recipe["attributes"]["properties"] = [
            {
                "key": "cleanroom",
                "value": "cleanroom",
                "value_type": "cleanroom_type",
            }
        ]
        cleanroom = node(
            "rg:test:constraint:cleanroom",
            "constraint",
            {
                "constraint_kind": "cleanroom_recipe_envelope",
                "clean_multiblocks_effective": False,
                "machine_is_cleanroom_receiver": True,
            },
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [direct(self.machine, "has_constraint", cleanroom, 3)]
            ),
            self.authority_registry,
        )
        definition = result["definitions"][0]
        self.assertEqual("resolved", definition["status"])
        self.assertEqual("conditional", definition["applicability"])
        self.assertEqual(
            "compatible-cleanroom-provider-required",
            definition["facts"]["result"],
        )
        cleanroom["attributes"]["clean_multiblocks_effective"] = True
        bypassed = projection.normalize_infrastructure_candidates(
            self.candidates(
                [direct(self.machine, "has_constraint", cleanroom, 30)]
            ),
            self.authority_registry,
        )
        self.assertEqual(
            "not-applicable",
            bypassed["definitions"][0]["applicability"],
        )
        self.assertEqual(
            "bypassed-for-multiblock-by-effective-config",
            bypassed["definitions"][0]["facts"]["result"],
        )
        cleanroom["attributes"]["clean_multiblocks_effective"] = False
        cleanroom["attributes"]["machine_is_cleanroom_receiver"] = False
        rejected = projection.normalize_infrastructure_candidates(
            self.candidates(
                [direct(self.machine, "has_constraint", cleanroom, 31)]
            ),
            self.authority_registry,
        )
        self.assertEqual("resolved", rejected["definitions"][0]["status"])
        self.assertEqual("always", rejected["definitions"][0]["applicability"])
        self.assertEqual(
            "selected-machine-rejects-cleanroom-recipe",
            rejected["definitions"][0]["facts"]["result"],
        )

    def test_steam_no_energy_and_generator_direction_are_distinct(self) -> None:
        self.machine["attributes"]["recipe_logic_class"] = (
            "supersymmetry.api.capability.impl."
            "NoEnergyMultiblockRecipeLogic"
        )
        steam = node(
            "rg:test:constraint:steam",
            "constraint",
            {
                "constraint_kind": "implicit_steam_energy_carrier",
                "carrier": "fluid:steam",
                "conversion_rate": Decimal("1.0"),
            },
        )
        no_energy = node(
            "rg:test:constraint:no-energy",
            "constraint",
            {
                "constraint_kind": "no_energy_recipe_logic",
                "carrier": "none",
                "draw_energy_semantics": (
                    "pinned implementation performs no power transfer"
                ),
            },
        )
        output_energy = node(
            "rg:test:energy:output",
            "energy_carrier",
            {"carrier": "gregtech:eu"},
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(
                        self.machine,
                        "has_constraint",
                        steam,
                        31,
                    ),
                    direct(
                        self.machine,
                        "has_constraint",
                        no_energy,
                        32,
                    ),
                    direct(
                        self.machine,
                        "produces_energy",
                        output_energy,
                        33,
                    ),
                ]
            ),
            self.authority_registry,
        )
        by_kind = {row["kind"]: row for row in result["definitions"]}
        self.assertEqual("resolved", by_kind["steam-energy"]["status"])
        self.assertEqual(
            ["CIT-SUSYCORE-NO-ENERGY-MULTIBLOCK-RECIPE-LOGIC"],
            by_kind["energy-input"]["citation_ids"],
        )
        self.assertEqual(
            "not-applicable",
            by_kind["energy-input"]["applicability"],
        )
        outcomes = {
            row["predicate"]: row["outcomes"][0]
            for row in result["candidate_dispositions"]
        }
        self.assertEqual(
            "energy-output-is-not-an-input-prerequisite",
            outcomes["produces_energy"]["reason"],
        )
        self.assertEqual(
            "excluded",
            outcomes["produces_energy"]["disposition"],
        )

    def test_environment_dimension_and_infinite_water_are_source_bound(
        self,
    ) -> None:
        ambient = node(
            "rg:test:constraint:ambient-water",
            "constraint",
            {
                "constraint_kind": "ambient_water_formula",
                "period_ticks": 20,
                "dimension_admission": "reject water-vaporizing providers",
            },
        )
        infinite = node(
            "rg:test:constraint:infinite-water",
            "constraint",
            {
                "constraint_kind": "infinite_water_supply",
                "service": "infinite_water_tank",
            },
        )
        dimension = node(
            "rg:test:dimension:susy:planet",
            "dimension",
            {
                "dimension_id": 800,
                "representation": "susy_loaded_world_planet",
            },
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(
                        self.machine,
                        "has_constraint",
                        ambient,
                        34,
                    ),
                    direct(
                        self.machine,
                        "has_constraint",
                        infinite,
                        35,
                    ),
                    direct(
                        self.machine,
                        "spawns_in",
                        dimension,
                        36,
                    ),
                ]
            ),
            self.authority_registry,
        )
        self.assertEqual("partial", result["status"])
        environments = [
            row
            for row in result["definitions"]
            if row["kind"] == "environment"
        ]
        self.assertEqual(2, len(environments))
        self.assertTrue(
            all(row["status"] == "resolved" for row in environments)
        )
        dimension_definition = next(
            row
            for row in result["definitions"]
            if row["kind"] == "dimension"
        )
        self.assertEqual("partial", dimension_definition["status"])
        self.assertEqual(
            "conditional",
            dimension_definition["applicability"],
        )

    def test_known_recipe_properties_are_context_not_duplicate_requirements(
        self,
    ) -> None:
        cleanroom_property = node(
            "rg:test:constraint:cleanroom-property",
            "constraint",
            {
                "constraint_kind": "gt_recipe_property",
                "property": {
                    "key": "cleanroom",
                    "value": "cleanroom",
                    "value_type": "cleanroom_type",
                },
            },
        )
        primitive_property = node(
            "rg:test:constraint:primitive-property",
            "constraint",
            {
                "constraint_kind": "gt_recipe_property",
                "property": {
                    "key": "primitive_property",
                    "value": True,
                    "value_type": "boolean",
                },
            },
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(
                        self.machine,
                        "has_constraint",
                        cleanroom_property,
                        37,
                    ),
                    direct(
                        self.machine,
                        "has_constraint",
                        primitive_property,
                        38,
                    ),
                ]
            ),
            self.authority_registry,
        )
        self.assertEqual("answered", result["status"])
        self.assertEqual([], result["definitions"])
        self.assertEqual(
            {"excluded": 2},
            result["summary"]["candidate_disposition_counts"],
        )

    def test_dimension_source_authority_is_owner_specific(self) -> None:
        generic_dimension = node(
            "rg:test:dimension:overworld",
            "dimension",
            {
                "dimension_id": 0,
                "representation": "gt_named_dimension",
            },
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [
                    direct(
                        self.machine,
                        "spawns_in",
                        generic_dimension,
                        39,
                    )
                ]
            ),
            self.authority_registry,
        )
        self.assertEqual("unresolved", result["definitions"][0]["status"])
        self.assertEqual([], result["definitions"][0]["citation_ids"])
        self.assertEqual(
            "missing-dimension-owner-source-citation",
            result["candidate_dispositions"][0]["outcomes"][0][
                "reason"
            ],
        )

    def test_unknown_constraint_is_retained_as_unresolved(self) -> None:
        unknown = node(
            "rg:test:constraint:unknown",
            "constraint",
            {"constraint_kind": "future_machine_rule"},
        )
        result = projection.normalize_infrastructure_candidates(
            self.candidates(
                [direct(self.machine, "has_constraint", unknown, 4)]
            ),
            self.authority_registry,
        )
        self.assertEqual("partial", result["status"])
        self.assertEqual(1, len(result["unresolved"]))
        self.assertEqual(
            "unresolved",
            result["candidate_dispositions"][0]["outcomes"][0]["disposition"],
        )

    def test_semantic_recomputation_rejects_drift(self) -> None:
        candidates = self.candidates(
            [direct(self.machine, "uses_energy", self.energy, 5)]
        )
        result = projection.normalize_infrastructure_candidates(
            candidates,
            self.authority_registry,
        )
        drift = deepcopy(result)
        drift["definitions"][0]["facts"]["recipe_eut"] = 30
        with self.assertRaisesRegex(
            projection.InfrastructureProjectionError,
            "differs from policy recomputation",
        ):
            projection.validate_infrastructure_projection(
                drift,
                candidates,
                self.authority_registry,
            )


if __name__ == "__main__":
    unittest.main()
