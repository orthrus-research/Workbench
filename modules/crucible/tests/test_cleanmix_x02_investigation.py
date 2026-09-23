from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MATRIX = (
    ROOT / "profiles/platforms/cleanroom/mixins"
    / "cleanmix-0.7.0-runtime-regression-matrix-v1.json"
)
INVESTIGATION_REPORT = (
    ROOT / "profiles/platforms/cleanroom/mixins"
    / "cleanmix-x02-investigation-report-v1.json"
)


class CleanMixX02InvestigationTests(unittest.TestCase):
    def test_report_binds_the_immutable_v1_and_x01_baseline(self) -> None:
        report = json.loads(INVESTIGATION_REPORT.read_bytes())
        matrix = json.loads(MATRIX.read_bytes())
        self.assertEqual(matrix["matrix_id"], report["baseline"]["matrix_id"])
        self.assertEqual(
            hashlib.sha256(MATRIX.read_bytes()).hexdigest(),
            report["baseline"]["matrix_file_sha256"],
        )
        self.assertEqual(
            "0ee8b92472de69f3920d5818deef3ccf14df3403",
            report["baseline"]["repository_commit"],
        )
        self.assertEqual(
            "cleanmix-p0-runtime-matrix-execution:sha256:"
            "4558726335039b53c0c4d228f26376b3a79e54530824e2f21c78d1d2ba91c7db",
            report["baseline"]["x01_execution_id"],
        )

    def test_report_is_content_addressed_and_keeps_v1_immutable(self) -> None:
        report = json.loads(INVESTIGATION_REPORT.read_bytes())
        material = deepcopy(report)
        report_id = material.pop("report_id")
        canonical = json.dumps(
            material,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertEqual(
            "cleanmix-x02-investigation:sha256:"
            + hashlib.sha256(canonical).hexdigest(),
            report_id,
        )
        self.assertEqual("complete", report["state"])
        self.assertEqual(
            "fixture_oracle_false_positive",
            report["dispositions"]["three_deep_xpass"]["result"],
        )
        self.assertEqual(
            "historical_defect_not_reproduced",
            report["dispositions"]["handler_first_entry_oracle"]["result"],
        )
        self.assertEqual(
            "a40d43d6b17f9fca653a7c777ff3c4d56c7adaa3b25297d99934b11d1f4c42fb",
            hashlib.sha256(MATRIX.read_bytes()).hexdigest(),
        )

    def test_retained_rows_require_direct_application_and_final_bytes(self) -> None:
        report = json.loads(INVESTIGATION_REPORT.read_bytes())
        rows = report["evidence"]["rows"]
        self.assertEqual(5, len(rows))
        for row in rows:
            with self.subTest(launch_id=row["launch_id"]):
                self.assertEqual("conforming", row["behavior"])
                self.assertTrue(
                    row["direct_receipt_id"].startswith(
                        "crucible-mixin-direct-application:sha256:"
                    )
                )
                self.assertEqual(
                    len(row["handler_counts"]),
                    len(row["final_definitions"]),
                )
                self.assertTrue(all(count == 1 for count in row["handler_counts"]))
                for definition in row["final_definitions"]:
                    self.assertGreater(definition["size_bytes"], 0)
                    self.assertEqual(64, len(definition["sha256"]))


if __name__ == "__main__":
    unittest.main()
