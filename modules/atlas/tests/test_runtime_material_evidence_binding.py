from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "atlas/src",
    ROOT.parent / "profiles/packs/supersymmetry/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_atlas.runtime_graph_domain_query import ProfileScope  # noqa: E402
from workbench_atlas.runtime_graph_material_classification import (  # noqa: E402
    MaterialClassificationBounds,
    MaterialClassificationResult,
)
from workbench_atlas.runtime_material_evidence_binding import (  # noqa: E402
    RuntimeMaterialEvidenceBindingError,
    build_runtime_material_evidence_binding,
    validate_runtime_material_evidence_binding,
)
from workbench_profile_supersymmetry.gtceu_material_classification import (  # noqa: E402
    GTCEU_MATERIAL_CLASSIFICATION_POLICY,
)


class RuntimeMaterialEvidenceBindingTests(unittest.TestCase):
    def classification(self) -> MaterialClassificationResult:
        return MaterialClassificationResult(
            scope=ProfileScope("COMMON_FINAL_STATE", "DEDICATED_SERVER"),
            policy=GTCEU_MATERIAL_CLASSIFICATION_POLICY,
            bounds=MaterialClassificationBounds(),
            rows=(),
            summary={"classification_status_counts": {}},
        )

    def binding(self) -> dict[str, object]:
        return build_runtime_material_evidence_binding(
            self.classification(),
            pack_profile_id="workbench-pack:supersymmetry",
            graph_database_sha256="a" * 64,
            graph_normalization_id="runtime-graph-normalization:test",
            crucible_snapshot_id="workbench-crucible-stage-snapshot:test",
            capture_id="capture:test",
            frozen_stage="COMMON_FINAL_STATE",
            manager_phase="FROZEN",
        )

    def test_binding_retains_classification_and_frozen_capture_identity(self) -> None:
        classification = self.classification()
        binding = self.binding()
        self.assertEqual(classification.sha256, binding["classification"]["sha256"])
        self.assertEqual("frozen", binding["frozen_runtime"]["freeze_state"])
        self.assertEqual("Crucible", binding["authority"]["runtime_owner"])
        self.assertEqual(
            binding,
            validate_runtime_material_evidence_binding(binding, classification),
        )

    def test_binding_rejects_a_different_classification(self) -> None:
        binding = self.binding()
        different = MaterialClassificationResult(
            scope=ProfileScope("COMMON_FINAL_STATE", "CLIENT"),
            policy=GTCEU_MATERIAL_CLASSIFICATION_POLICY,
            bounds=MaterialClassificationBounds(),
            rows=(),
            summary={"classification_status_counts": {}},
        )
        with self.assertRaises(RuntimeMaterialEvidenceBindingError):
            validate_runtime_material_evidence_binding(binding, different)


if __name__ == "__main__":
    unittest.main()
