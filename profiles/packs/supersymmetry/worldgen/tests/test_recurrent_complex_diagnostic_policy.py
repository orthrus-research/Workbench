from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_diagnostics import (  # noqa: E402
    parse_literal_diagnostic_policy,
)


POLICY_PATH = (
    ROOT
    / "profiles/packs/supersymmetry/worldgen/recurrent-complex-diagnostic-health-policy-v1.json"
)
RECURRENT_COMPLEX_SHA256 = (
    "253226e6c7efe61ae255df0cc2e19d1420945cb7e86f7cd79f51d5db10fd9de8"
)


class RecurrentComplexDiagnosticPolicyTests(unittest.TestCase):
    def test_exact_resource_failures_and_cascading_review_are_declared(self) -> None:
        policy = parse_literal_diagnostic_policy(
            json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        )
        self.assertEqual(policy["subject_artifact_sha256"], RECURRENT_COMPLEX_SHA256)
        fail_prefixes = {
            row["message_prefix"]
            for row in policy["rules"]
            if row["disposition"] == "fail"
        }
        self.assertEqual(
            fail_prefixes,
            {
                "Error reading resource: Unholy",
                "Error reading resource: TribalChest",
                "Error reading resource: PeacefulCrypt",
                "Error reading resource: Holy",
            },
        )
        review = [
            row for row in policy["rules"] if row["disposition"] == "review"
        ]
        self.assertEqual(len(review), 1)
        self.assertEqual(
            review[0]["message_prefix"],
            "Cascading chunk generation happening while ",
        )
        self.assertTrue(
            all(row["logger"] == "reccomplex" and row["level"] == "WARN" for row in policy["rules"])
        )


if __name__ == "__main__":
    unittest.main()
