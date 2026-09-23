from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[4]
CATALOG = (
    ROOT
    / "profiles/platforms/cleanroom/mixins"
    / "cleanroom-runtime-analysis-epochs-v1.json"
)
CANDIDATE_LOCK = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/candidate-lock-v1.json"
)
TOOLCHAIN_LOCK = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "transformer-toolchain-lock-v1.json"
)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CleanroomRuntimeAnalysisEpochTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.epochs = {row["epoch_id"]: row for row in cls.catalog["epochs"]}

    def test_catalog_is_content_addressed_and_has_one_capability_vocabulary(self) -> None:
        material = deepcopy(self.catalog)
        catalog_id = material.pop("catalog_id")
        self.assertEqual(
            "workbench-cleanroom-runtime-analysis-epoch-catalog:sha256:"
            + _canonical_sha256(material),
            catalog_id,
        )
        capabilities = self.catalog["common_capabilities"]
        self.assertEqual(sorted(capabilities), capabilities)
        self.assertEqual(len(capabilities), len(set(capabilities)))

    def test_cleanroom_and_cleanmix_versions_remain_separate_axes(self) -> None:
        exact = self.epochs[
            "cleanroom-0.6.8-alpha-cleanmix-0.7-foundation-providers"
        ]
        self.assertEqual("0.6.8-alpha", exact["selector"]["cleanroom"])
        self.assertEqual("0.7.0", exact["selector"]["cleanmix"])
        self.assertEqual("0.19.11", exact["selector"]["foundation"])
        self.assertEqual("implemented", exact["adapter"]["implementation_state"])
        self.assertEqual([], exact["adapter"]["pending_capabilities"])
        self.assertEqual(
            set(self.catalog["common_capabilities"]),
            set(exact["adapter"]["implemented_capabilities"])
            | set(exact["adapter"]["pending_capabilities"]),
        )

    def test_exact_epoch_binds_current_candidate_files(self) -> None:
        exact = self.epochs[
            "cleanroom-0.6.8-alpha-cleanmix-0.7-foundation-providers"
        ]
        binding = exact["candidate_binding"]
        self.assertEqual(
            hashlib.sha256(CANDIDATE_LOCK.read_bytes()).hexdigest(),
            binding["candidate_lock_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(TOOLCHAIN_LOCK.read_bytes()).hexdigest(),
            binding["transformer_toolchain_lock_sha256"],
        )
        toolchain = json.loads(TOOLCHAIN_LOCK.read_text(encoding="utf-8"))
        artifacts = {row["coordinate"]: row for row in toolchain["artifacts"]}
        self.assertEqual(
            artifacts["com.cleanroommc:cleanmix:0.7.0"]["sha256"],
            binding["cleanmix_artifact_sha256"],
        )
        self.assertEqual(
            artifacts["top.outlands:foundation:0.19.11"]["sha256"],
            binding["foundation_artifact_sha256"],
        )

    def test_catalog_models_the_ownership_transition_without_erasing_bridges(self) -> None:
        legacy = self.epochs[
            "cleanroom-0.5.17-alpha-mixinbooter-launchwrapper"
        ]
        middle = self.epochs[
            "cleanroom-0.6.0-alpha-cleanmix-launchwrapper-providers"
        ]
        exact = self.epochs[
            "cleanroom-0.6.8-alpha-cleanmix-0.7-foundation-providers"
        ]
        self.assertIn("MixinBooterPlugin", legacy["ownership"]["bootstrap"])
        self.assertIn("CleanMixService", middle["ownership"]["service"])
        self.assertIn(
            "AbstractMixinServiceLaunchWrapper",
            middle["ownership"]["transformer_provider"],
        )
        self.assertIn(
            "FoundationTransformerProvider",
            exact["ownership"]["transformer_provider"],
        )
        self.assertTrue(any("ownership transition" in row for row in exact["bridges"]))

    def test_future_cleanroom_0_7_is_not_claimed_without_an_exact_candidate(self) -> None:
        target = self.catalog["unresolved_targets"][0]
        self.assertEqual("0.7.x", target["cleanroom_version_family"])
        self.assertEqual("no-exact-candidate", target["state"])
        self.assertIn("CleanMix 0.7.x is a separate version axis", target["admission_requirement"])


if __name__ == "__main__":
    unittest.main()
