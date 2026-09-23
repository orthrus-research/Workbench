from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[4]
CANDIDATE_DIRECTORY = (
    ROOT / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
)
CANDIDATE_LOCK = CANDIDATE_DIRECTORY / "candidate-lock-v1.json"
TOOLCHAIN_LOCK = CANDIDATE_DIRECTORY / "transformer-toolchain-lock-v1.json"
POLICY = (
    ROOT
    / "profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-policy-v1.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TransformerToolchainLockTests(unittest.TestCase):
    def test_preserves_candidate_lock_v1_and_binds_exact_policy(self) -> None:
        lock = json.loads(TOOLCHAIN_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(
            "d6f34886222a4d9e376ef6b1144a3f00e70ce14e8c779363997e8aa206aaf541",
            _sha256(CANDIDATE_LOCK),
        )
        self.assertEqual(_sha256(CANDIDATE_LOCK), lock["binding"]["candidate_lock_sha256"])
        self.assertEqual(_sha256(POLICY), lock["binding"]["doctor_policy_sha256"])
        self.assertEqual(
            "workbench-cleanroom-mixin-doctor-policy-v1",
            lock["binding"]["doctor_policy_id"],
        )
        self.assertEqual(
            "not_claimed", lock["evidence_boundary"]["runtime_receipt"]
        )

    def test_transformation_critical_artifacts_are_byte_locked(self) -> None:
        lock = json.loads(TOOLCHAIN_LOCK.read_text(encoding="utf-8"))
        artifacts = lock["artifacts"]
        self.assertEqual(11, len(artifacts))
        coordinates = [row["coordinate"] for row in artifacts]
        filenames = [row["filename"] for row in artifacts]
        hashes = [row["sha256"] for row in artifacts]
        self.assertEqual(len(coordinates), len(set(coordinates)))
        self.assertEqual(len(filenames), len(set(filenames)))
        self.assertEqual(len(hashes), len(set(hashes)))
        for row in artifacts:
            self.assertRegex(row["sha256"], SHA256_RE)
            self.assertGreater(row["size"], 0)
            self.assertTrue(row["url"].startswith("https://"))

        by_coordinate = {row["coordinate"]: row for row in artifacts}
        expected = {
            "com.cleanroommc:cleanmix:0.7.0": (
                "a41daa71398e948bc09fa578ae2460be639df0fa82fccdb630b316418d5a4a36",
                1094214,
            ),
            "com.cleanroommc:mixinextras-common:0.5.5": (
                "6aa293132763b356fe6de570c18535acb93ab5509e0dd2e50fec8747373765e6",
                725959,
            ),
            "top.outlands:foundation:0.19.11": (
                "a9f5cf9cb54715edf22d6bcb49bae50f281f818b1731f53f22aab85a48a7b54b",
                55607,
            ),
        }
        for coordinate, (digest, size) in expected.items():
            self.assertEqual(digest, by_coordinate[coordinate]["sha256"])
            self.assertEqual(size, by_coordinate[coordinate]["size"])

    def test_policy_rule_graph_is_closed_and_matches_scanner_fact_roots(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        expected_facts = {
            "artifact.compatibility_metadata",
            "artifact.embedded_packages",
            "artifact.mixin_configs",
            "artifact.mixins",
            "artifact.overlaps",
            "artifact.owner_id_collisions",
            "artifact.plugin",
            "artifact.refmap",
            "artifact.registration_routes",
        }
        self.assertEqual(expected_facts, set(policy["doctor_rules"]))
        rules = [
            rule
            for group in policy["doctor_rules"].values()
            for rule in group["rules"]
        ]
        rule_ids = [row["finding_id"] for row in rules]
        self.assertEqual(41, len(rule_ids))
        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        evidence_ids = {row["id"] for row in policy["evidence_sources"]}
        for epoch in policy["behavior_epochs"]:
            self.assertTrue(set(epoch["evidence_refs"]) <= evidence_ids)
        for rule in rules:
            self.assertIn(rule["result"]["disposition"], {"accept", "review", "reject"})
            self.assertIn(rule["result"]["severity"], {"info", "warning", "error"})


if __name__ == "__main__":
    unittest.main()
