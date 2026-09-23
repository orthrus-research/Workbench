#!/usr/bin/env python3

"""Supersymmetry policy for source-to-frozen material comparison."""

from __future__ import annotations

from typing import Any, Mapping

from workbench_atlas.runtime_graph_material_classification import MaterialClassificationResult
from workbench_atlas_projection.material_source_runtime import (
    MaterialSourceRuntimeComparisonPolicy,
    compare_material_source_to_runtime,
)

from workbench_profile_supersymmetry.gtceu_material_semantics import (
    GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2,
)


_FLAG_FORM_PREFIXES = {
    "GENERATE_SPUTTERING_TARGET": ("target",),
    "force_generate_block": ("block",),
    "generate_bolt_screw": ("bolt", "screw"),
    "generate_catalyst_bed": ("catalystBed",),
    "generate_catalyst_pellet": ("catalystPellet",),
    "generate_concentrate": ("dustConcentrate",),
    "generate_dense": ("plateDense",),
    "generate_double_plate": ("plateDouble",),
    "generate_fiber": ("fiber",),
    "generate_fine_wire": ("wireFine",),
    "generate_flotated": ("dustFlotated",),
    "generate_foil": ("foil",),
    "generate_frame": ("frameGt",),
    "generate_gear": ("gear",),
    "generate_lens": ("lens",),
    "generate_long_rod": ("stickLong",),
    "generate_pins": ("pin",),
    "generate_plate": ("plate",),
    "generate_ring": ("ring",),
    "generate_rod": ("stick",),
    "generate_rotor": ("rotor",),
    "generate_round": ("round",),
    "generate_sifted": ("dustSifted",),
    "generate_small_gear": ("gearSmall",),
    "generate_spring": ("spring",),
    "generate_spring_small": ("springSmall",),
    "generate_thread": ("thread",),
    "generate_wet_dust": ("dustWet",),
    "generate_wet_fiber": ("fiberWet",),
    "superalloy": ("electrode",),
}


GTCEU_MATERIAL_SOURCE_RUNTIME_COMPARISON_POLICY_V1 = (
    MaterialSourceRuntimeComparisonPolicy(
        policy_id=(
            "workbench://profiles/supersymmetry/atlas/material-source-runtime/"
            "gtceu-2.8.10-susycore-b5ee1120-v1"
        ),
        pack_profile_id="workbench-pack:supersymmetry",
        platform_profile_id="minecraft-1.12.2/gtceu-2.8.10-beta",
        physical_side="DEDICATED_SERVER",
        runtime_policy_id=(
            GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2.policy_id
        ),
        runtime_policy_sha256=(
            GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2.sha256
        ),
        admitted_source_kinds=("workbench-pack-material-program-v2",),
        flag_form_prefixes=tuple(
            sorted(
                (
                    flag,
                    tuple(sorted(prefixes, key=lambda value: value.encode("utf-8"))),
                )
                for flag, prefixes in _FLAG_FORM_PREFIXES.items()
            )
        ),
        semantic_source_files=(
            (
                "SRC-GTCEU@9fe140febe8747bbe2f06dfd570421331ec06f4b/"
                "src/main/java/gregtech/api/unification/ore/OrePrefix.java",
                "867b5eaf5955db119381242b91c37633650f61d499ab86387f0042a4257592a1",
            ),
            (
                "SRC-SUSYCORE@b5ee1120df93c95b66e6a4dfff35582928f2d348/"
                "src/main/java/supersymmetry/api/unification/material/info/"
                "SuSyMaterialFlags.java",
                "9094b1610abd8e819ea016c03c471b88f04896af0ac55386a3a74ac960b98594",
            ),
            (
                "SRC-SUSYCORE@b5ee1120df93c95b66e6a4dfff35582928f2d348/"
                "src/main/java/supersymmetry/api/unification/ore/SusyOrePrefix.java",
                "b54817618a12ce2ed991b66fe9683798f3b99efc7a0718d6c2a74dd546b412e7",
            ),
        ),
    )
)


def compare_supersymmetry_material_source_to_runtime(
    source_feed: Mapping[str, Any],
    runtime_classification: MaterialClassificationResult,
    runtime_evidence_binding: Mapping[str, Any],
) -> dict[str, Any]:
    return compare_material_source_to_runtime(
        source_feed,
        runtime_classification,
        runtime_evidence_binding,
        GTCEU_MATERIAL_SOURCE_RUNTIME_COMPARISON_POLICY_V1,
    )


__all__ = [
    "GTCEU_MATERIAL_SOURCE_RUNTIME_COMPARISON_POLICY_V1",
    "compare_supersymmetry_material_source_to_runtime",
]
