#!/usr/bin/env python3

"""Supersymmetry authority for GTCEu 2.8.10 material classification."""

from __future__ import annotations

from workbench_atlas.runtime_graph_domain_query import ProfileScope
from workbench_atlas.runtime_graph_material_classification import (
    DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS,
    MaterialClassificationBounds,
    MaterialClassificationPolicy,
    MaterialClassificationResult,
    classify_materials,
)
from workbench_atlas.runtime_graph_query import RuntimeGraphReader


GTCEU_MATERIAL_CLASSIFICATION_POLICY = MaterialClassificationPolicy(
    policy_id=(
        "workbench://profiles/supersymmetry/atlas/material-classification/"
        "gtceu-2.8.10-v1"
    ),
    platform_profile="minecraft-1.12.2/gtceu-2.8.10-beta",
    admitted_material_adapters=("gt_materials",),
    base_property_keys=("dust", "empty", "fluid", "gem", "ingot"),
    capability_property_keys=(
        "blast",
        "fluid_pipe",
        "item_pipe",
        "ore",
        "polymer",
        "rotor",
        "tool",
        "wire",
        "wood",
    ),
    property_dependencies=(
        ("blast", ("ingot",)),
        ("gem", ("dust",)),
        ("ingot", ("dust",)),
        ("ore", ("dust",)),
        ("polymer", ("dust", "ingot")),
        ("rotor", ("ingot",)),
        ("wire", ("dust",)),
        ("wood", ("dust",)),
    ),
    property_any_dependencies=(
        ("fluid_pipe", ("ingot", "wood")),
        ("item_pipe", ("ingot", "wood")),
        ("tool", ("gem", "ingot")),
    ),
    property_fallback_dependencies=(
        ("fluid_pipe", ("ingot",)),
        ("item_pipe", ("ingot",)),
        ("tool", ("ingot",)),
    ),
    property_incompatibilities=(
        ("blast", "empty"),
        ("dust", "empty"),
        ("empty", "fluid"),
        ("empty", "fluid_pipe"),
        ("empty", "gem"),
        ("empty", "ingot"),
        ("empty", "item_pipe"),
        ("empty", "ore"),
        ("empty", "polymer"),
        ("empty", "rotor"),
        ("empty", "tool"),
        ("empty", "wire"),
        ("empty", "wood"),
        ("fluid_pipe", "item_pipe"),
        ("gem", "ingot"),
    ),
    flag_categories=(
        ("blast_furnace_calcite_double", ("process_policy",)),
        ("blast_furnace_calcite_triple", ("process_policy",)),
        ("crystallizable", ("behavior",)),
        ("decomposition_by_centrifuging", ("process_policy",)),
        ("decomposition_by_electrolyzing", ("process_policy",)),
        ("disable_decomposition", ("process_policy", "restriction")),
        ("disable_ore_block", ("restriction",)),
        ("exclude_block_crafting_by_hand_recipes", ("restriction",)),
        ("exclude_block_crafting_recipes", ("restriction",)),
        ("exclude_plate_compressor_recipe", ("restriction",)),
        ("explosive", ("behavior",)),
        ("flammable", ("behavior",)),
        ("force_generate_block", ("form_generation",)),
        ("generate_bolt_screw", ("form_generation",)),
        ("generate_dense", ("form_generation",)),
        ("generate_double_plate", ("form_generation",)),
        ("generate_fine_wire", ("form_generation",)),
        ("generate_foil", ("form_generation",)),
        ("generate_frame", ("form_generation",)),
        ("generate_gear", ("form_generation",)),
        ("generate_lens", ("form_generation",)),
        ("generate_long_rod", ("form_generation",)),
        ("generate_plate", ("form_generation",)),
        ("generate_ring", ("form_generation",)),
        ("generate_rod", ("form_generation",)),
        ("generate_rotor", ("form_generation",)),
        ("generate_round", ("form_generation",)),
        ("generate_small_gear", ("form_generation",)),
        ("generate_spring", ("form_generation",)),
        ("generate_spring_small", ("form_generation",)),
        ("glowing", ("behavior",)),
        ("high_sifter_output", ("process_policy",)),
        ("is_magnetic", ("behavior",)),
        ("mortar_grindable", ("process_policy",)),
        ("no_smashing", ("restriction",)),
        ("no_smelting", ("restriction",)),
        ("no_unification", ("restriction",)),
        ("no_working", ("restriction",)),
        ("solder_material", ("process_policy",)),
        ("solder_material_bad", ("process_policy",)),
        ("solder_material_good", ("process_policy",)),
        ("sticky", ("behavior",)),
    ),
    flag_dependencies=(
        ("exclude_plate_compressor_recipe", ("generate_plate",)),
        ("generate_bolt_screw", ("generate_rod",)),
        ("generate_dense", ("generate_plate",)),
        ("generate_double_plate", ("generate_plate",)),
        ("generate_fine_wire", ("generate_foil",)),
        ("generate_foil", ("generate_plate",)),
        ("generate_frame", ("generate_rod",)),
        ("generate_gear", ("generate_plate", "generate_rod")),
        ("generate_lens", ("generate_plate",)),
        ("generate_long_rod", ("generate_rod",)),
        ("generate_ring", ("generate_rod",)),
        (
            "generate_rotor",
            ("generate_bolt_screw", "generate_plate", "generate_ring"),
        ),
        ("generate_small_gear", ("generate_plate", "generate_rod")),
        ("generate_spring", ("generate_long_rod",)),
        ("generate_spring_small", ("generate_rod",)),
    ),
    flag_property_requirements=(
        ("blast_furnace_calcite_double", ("dust",)),
        ("blast_furnace_calcite_triple", ("dust",)),
        ("crystallizable", ("gem",)),
        ("disable_ore_block", ("ore",)),
        ("exclude_block_crafting_by_hand_recipes", ("dust",)),
        ("exclude_block_crafting_recipes", ("dust",)),
        ("exclude_plate_compressor_recipe", ("dust",)),
        ("force_generate_block", ("dust",)),
        ("generate_bolt_screw", ("dust",)),
        ("generate_dense", ("dust",)),
        ("generate_double_plate", ("ingot",)),
        ("generate_fine_wire", ("ingot",)),
        ("generate_foil", ("ingot",)),
        ("generate_frame", ("dust",)),
        ("generate_gear", ("dust",)),
        ("generate_lens", ("gem",)),
        ("generate_long_rod", ("dust",)),
        ("generate_plate", ("dust",)),
        ("generate_ring", ("ingot",)),
        ("generate_rod", ("dust",)),
        ("generate_rotor", ("ingot",)),
        ("generate_round", ("ingot",)),
        ("generate_small_gear", ("ingot",)),
        ("generate_spring", ("ingot",)),
        ("generate_spring_small", ("ingot",)),
        ("high_sifter_output", ("gem", "ore")),
        ("is_magnetic", ("ingot",)),
        ("mortar_grindable", ("dust",)),
        ("no_smashing", ("dust",)),
        ("no_smelting", ("dust",)),
        ("no_working", ("dust",)),
        ("solder_material", ("fluid",)),
        ("solder_material_bad", ("fluid",)),
        ("solder_material_good", ("fluid",)),
    ),
    property_implied_flags=(
        (
            "polymer",
            ("disable_decomposition", "flammable", "no_smashing"),
        ),
        ("wood", ("flammable",)),
    ),
    generation_constraint_kind="ore_prefix_material_generation",
)


def classify_supersymmetry_materials(
    reader: RuntimeGraphReader,
    scope: ProfileScope,
    bounds: MaterialClassificationBounds = (
        DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS
    ),
) -> MaterialClassificationResult:
    """Classify the exact frozen GTCEu material inventory for one scope."""

    return classify_materials(
        reader,
        scope,
        GTCEU_MATERIAL_CLASSIFICATION_POLICY,
        bounds,
    )


__all__ = [
    "GTCEU_MATERIAL_CLASSIFICATION_POLICY",
    "classify_supersymmetry_materials",
]
