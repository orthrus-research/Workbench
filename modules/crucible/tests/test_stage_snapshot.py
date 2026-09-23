from __future__ import annotations

import copy
import unittest

from workbench_crucible_stage_snapshot import (
    StageSnapshotError,
    build_stage_snapshot,
    effect_observation,
    registry_observation,
    validate_stage_snapshot,
)


DESCRIPTOR = {
    "domain": "material-fluid-state",
    "kind": "material-fluid-state",
    "key": {"material": "example:mixture", "state": "gas"},
}


class StageSnapshotTests(unittest.TestCase):
    def test_snapshot_retains_registry_and_repeated_effects(self) -> None:
        registry = registry_observation(
            stage="material",
            semantic_descriptor=DESCRIPTOR,
            registry_state={"registered": True},
            provenance={"receipt_id": "receipt:1"},
        )
        effects = [
            effect_observation(
                stage="material",
                semantic_descriptor=DESCRIPTOR,
                operation="register-fluid-state",
                effect_state={"result": result},
                provenance={"receipt_id": f"receipt:{index}"},
            )
            for index, result in enumerate(("accepted", "duplicate-attempt"), 2)
        ]
        snapshot = build_stage_snapshot(
            pack_profile_id="pack",
            platform_profile_id="platform",
            stage="material",
            registry=[registry],
            effects=effects,
            capture={"capture_id": "capture:1"},
        )
        self.assertEqual("Crucible", snapshot["authority"]["owner"])
        self.assertEqual(2, snapshot["summary"]["effect_records"])
        self.assertEqual("none", snapshot["authority"]["playability_authority"])

    def test_record_cannot_escape_bound_stage(self) -> None:
        registry = registry_observation(
            stage="postInit",
            semantic_descriptor=DESCRIPTOR,
            registry_state={"registered": True},
            provenance={},
        )
        snapshot = build_stage_snapshot(
            pack_profile_id="pack",
            platform_profile_id="platform",
            stage="postInit",
            registry=[registry],
            effects=[],
            capture={"capture_id": "capture:1"},
        )
        tampered = copy.deepcopy(snapshot)
        tampered["registry"][0]["stage"] = "runtime"
        with self.assertRaises(StageSnapshotError):
            validate_stage_snapshot(tampered)


if __name__ == "__main__":
    unittest.main()
