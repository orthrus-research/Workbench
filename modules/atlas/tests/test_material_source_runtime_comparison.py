#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/atlas/src",
    ROOT / "modules/pack-program-studio/src",
    ROOT / "modules/crucible/src",
    ROOT / "profiles/packs/supersymmetry/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_profile_supersymmetry.gtceu_material_comparison import (  # noqa: E402
    GTCEU_MATERIAL_SOURCE_RUNTIME_COMPARISON_POLICY_V1,
)
from workbench_profile_supersymmetry.gtceu_material_semantics import (  # noqa: E402
    GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2,
)
from workbench_atlas.runtime_graph_domain_query import ProfileScope  # noqa: E402
from workbench_atlas.runtime_graph_material_classification import (  # noqa: E402
    DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS,
    MaterialClassificationResult,
)
from workbench_atlas.runtime_material_evidence_binding import (  # noqa: E402
    build_runtime_material_evidence_binding,
)
from workbench_atlas_projection.material_source_runtime import (  # noqa: E402
    MaterialSourceRuntimeComparisonError,
    compare_material_source_to_runtime,
)
from workbench_pack_program_studio import (  # noqa: E402
    build_source_declarations,
    source_declaration,
)


POLICY = GTCEU_MATERIAL_SOURCE_RUNTIME_COMPARISON_POLICY_V1
RUNTIME_POLICY = GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2


def _registration(resource: str, numeric_id: int) -> dict[str, object]:
    return source_declaration(
        semantic_descriptor={
            "domain": "material",
            "kind": "material-registration",
            "key": {"resource_location": resource},
        },
        attributes={
            "source_role": "direct-registration",
            "identity": {
                "resource_location": resource,
                "numeric_id": numeric_id,
            },
            "declared_material_core": {
                "identity": {
                    "resource_location": resource,
                    "numeric_id": numeric_id,
                },
                "expected_verified_closure": {
                    "state": "exact-static",
                    "closure_policy_id": RUNTIME_POLICY.policy_id,
                    "closure_policy_sha256": RUNTIME_POLICY.sha256,
                    "properties": {"keys": ["dust"]},
                    "flags": {"names": ["generate_plate"]},
                },
                "composition": {
                    "basis": "unspecified",
                    "components": [],
                    "elements": [],
                },
                "presentation": [],
            },
            "form_lens": {"fluid_storage_relationships": []},
        },
        lifecycle={"stage": "material"},
        provenance={
            "authority": "Pack Program Studio",
            "source": {"path": "material/Test.groovy"},
        },
    )


def _mutation(resource: str) -> dict[str, object]:
    return source_declaration(
        semantic_descriptor={
            "domain": "material",
            "kind": "material-mutation",
            "key": {
                "resource_location": resource,
                "source_occurrence": "classes/ChangeFlags.groovy#char=10",
            },
        },
        attributes={
            "source_role": "material-mutation",
            "target": {"resource_location": resource},
            "operation": "add-flags",
            "constraints": {"must_include_flags": ["generate_plate"]},
            "resolution_state": "exact-static",
        },
        lifecycle={"stage": "material-event"},
        provenance={
            "authority": "Pack Program Studio",
            "source": {"path": "classes/ChangeFlags.groovy"},
        },
    )


def _runtime_row(
    resource: str,
    numeric_id: int,
    *,
    plate: bool = True,
) -> dict[str, object]:
    material_id = f"material:{resource}"
    flags = ["generate_plate"] if plate else []
    forms = []
    if plate:
        forms.append(
            {
                "form_kind": "item_variant",
                "node_kind": "item_variant",
                "form_node_id": f"item:{resource}:plate",
                "relationship_id": f"edge:{resource}:plate",
                "relationship_adapter": "gt_materials",
                "prefix_name": "plate",
                "attributes": {"prefix_name": "plate"},
            }
        )
    return {
        "material_id": material_id,
        "source_adapter": "gt_materials",
        "classification_status": "exact",
        "frontier_issue_codes": [],
        "core_sha256": "0" * 64,
        "core": {
            "registry_role": "persistent",
            "identity": {
                "registry_name": resource,
                "namespace": resource.partition(":")[0],
                "name": resource.partition(":")[2],
                "numeric_id": numeric_id,
                "numeric_id_authoritative": True,
                "storage_registry": {"mod_id": "gregtech", "network_id": 0},
                "observed_keys": {
                    "material-numeric-id": [str(numeric_id)],
                    "material-resource-location": [resource],
                },
            },
            "composition": {
                "basis": "unspecified",
                "chemical_formula": "",
                "components_initialized": True,
                "components": [],
                "elements": [],
            },
            "properties": {"keys": ["dust"]},
            "flags": {"names": flags},
            "presentation": {"rgb": 0, "icon_set": {"name": "dull"}},
        },
        "form_lens": {
            "prefix_generation": {"decision_count": 0, "status_counts": {}, "decisions": []},
            "realized": {
                "count": len(forms),
                "counts_by_kind": {
                    "fluid": 0,
                    "item_variant": len(forms),
                    "other": 0,
                },
                "forms": forms,
            },
        },
    }


def _inputs() -> tuple[dict[str, object], MaterialClassificationResult, dict[str, object]]:
    declarations = [
        _registration("susy:test", 1),
        _mutation("gregtech:steel"),
        _registration("susy:source_only", 3),
    ]
    source = build_source_declarations(
        program_id="workbench-pack-material-source-program-v2:fixture",
        pack_profile_id=POLICY.pack_profile_id,
        platform_profile_id=POLICY.platform_profile_id,
        source_sha256="1" * 64,
        declarations=declarations,
        source_kind="workbench-pack-material-program-v2",
    )
    classification = MaterialClassificationResult(
        scope=ProfileScope("COMMON_FINAL_STATE", POLICY.physical_side),
        policy=RUNTIME_POLICY,
        bounds=DEFAULT_MATERIAL_CLASSIFICATION_BOUNDS,
        rows=(
            _runtime_row("susy:test", 1),
            _runtime_row("gregtech:steel", 324),
            _runtime_row("gregtech:runtime_only", 999),
        ),
        summary={},
    )
    evidence = build_runtime_material_evidence_binding(
        classification,
        pack_profile_id=POLICY.pack_profile_id,
        graph_database_sha256="2" * 64,
        graph_normalization_id="fixture-normalization",
        crucible_snapshot_id="fixture-snapshot",
        capture_id="fixture-capture",
        frozen_stage="COMMON_FINAL_STATE",
        manager_phase="frozen",
    )
    return source, classification, evidence


class MaterialSourceRuntimeComparisonTests(unittest.TestCase):
    def test_registration_mutation_source_only_and_unattributed_runtime_are_separate(self) -> None:
        source, classification, evidence = _inputs()
        result = compare_material_source_to_runtime(
            source, classification, evidence, POLICY
        )
        rows = {row["resource_location"]: row for row in result["materials"]}
        self.assertEqual("parity", rows["susy:test"]["aggregate_state"])
        self.assertEqual(
            "compliant",
            rows["gregtech:steel"]["mutation_compliance"]["state"],
        )
        self.assertEqual("parity", rows["gregtech:steel"]["aggregate_state"])
        self.assertEqual("source-only", rows["susy:source_only"]["aggregate_state"])
        self.assertEqual(
            "runtime-only", rows["gregtech:runtime_only"]["aggregate_state"]
        )
        self.assertEqual(
            "unattributed-runtime", rows["gregtech:runtime_only"]["attribution"]
        )
        self.assertEqual(
            "not-applicable",
            rows["gregtech:runtime_only"]["execution_lineage"]["state"],
        )
        self.assertEqual(
            "not-established",
            rows["susy:test"]["execution_lineage"]["state"],
        )

    def test_expected_closure_policy_mismatch_is_rejected(self) -> None:
        source, classification, evidence = _inputs()
        changed = deepcopy(source)
        registration = changed["declarations"][0]
        registration["attributes"]["declared_material_core"][
            "expected_verified_closure"
        ]["closure_policy_sha256"] = "f" * 64
        # Rebuild through the authority owner so the feed remains internally valid.
        changed = build_source_declarations(
            program_id=changed["binding"]["program_id"],
            pack_profile_id=changed["binding"]["pack_profile_id"],
            platform_profile_id=changed["binding"]["platform_profile_id"],
            source_sha256=changed["binding"]["source_sha256"],
            declarations=[
                source_declaration(
                    semantic_descriptor=row["semantic_descriptor"],
                    attributes=row["attributes"],
                    lifecycle=row["lifecycle"],
                    provenance=row["provenance"],
                    source_effect_id=row["source_effect_id"],
                )
                for row in changed["declarations"]
            ],
            source_kind=changed["binding"]["source_kind"],
        )
        with self.assertRaisesRegex(
            MaterialSourceRuntimeComparisonError,
            "expected-closure policy differs",
        ):
            compare_material_source_to_runtime(
                changed, classification, evidence, POLICY
            )

if __name__ == "__main__":
    unittest.main()
