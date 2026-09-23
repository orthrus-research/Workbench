from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "profiles/platforms/cleanroom/fixtures/cleanmix-p0-regression"
RUNNER = ROOT / "profiles/platforms/cleanroom/tools/run_cleanmix_p0_matrix.py"
MATRIX = (
    ROOT / "profiles/platforms/cleanroom/mixins"
    / "cleanmix-0.7.0-runtime-regression-matrix-v1.json"
)
BUILD = (
    ROOT / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "worldgen-observatory-fixture/build.gradle"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class CleanMixP0RegressionFixtureTests(unittest.TestCase):
    def test_fixture_covers_phase_late_and_named_gap_rows(self) -> None:
        harness = _read(
            FIXTURE / "src/main/java/dev/workbench/cleanmixp0/P0Harness.java"
        )
        matrix = json.loads(MATRIX.read_bytes())
        p0 = {row["id"] for row in matrix["rows"] if row["priority"] == "P0"}
        non_handler = {row for row in p0 if not row.startswith("P0-HANDLER-")}
        self.assertEqual(7, len(non_handler))
        for row in non_handler:
            if row.startswith("P0-PHASE-SEQUENCE-"):
                self.assertIn('row.startsWith("P0-PHASE-SEQUENCE-")', harness)
            else:
                self.assertIn('row.equals("' + row + '")', harness)

        resources = {
            path.name for path in (FIXTURE / "src/main/resources").glob("*.json")
        }
        self.assertEqual(9, len(resources))
        self.assertIn("mixins.workbench.cleanmix-p0.detached-two.json", resources)
        self.assertIn("mixins.workbench.cleanmix-p0.detached-three.json", resources)
        self.assertTrue(all(name.startswith("mixins.workbench.cleanmix-p0.") for name in resources))

    def test_fixture_preserves_exact_failure_oracles(self) -> None:
        harness = _read(
            FIXTURE / "src/main/java/dev/workbench/cleanmixp0/P0Harness.java"
        )
        plugin = _read(
            FIXTURE
            / "src/main/java/dev/workbench/cleanmixp0/ReentrantConfigPlugin.java"
        )
        self.assertIn("class_identity_before", harness)
        self.assertIn("class_identity_after", harness)
        self.assertIn("trigger_failure_class", harness)
        self.assertIn("callback_info_null_count", harness)
        self.assertIn("base_handler_count", harness)
        self.assertIn("middle_handler_count", harness)
        self.assertIn("leaf_handler_count", harness)
        self.assertIn("target_defined_during_getMixins", plugin)
        self.assertIn("WORKBENCH_CLEANMIX_P0_REENTRANT_V1", plugin)

    def test_candidate_builds_both_runtime_fixture_archives(self) -> None:
        build = _read(BUILD)
        self.assertIn("cleanmixHandlerRegressionJar", build)
        self.assertIn("cleanmixP0RegressionJar", build)
        self.assertIn(
            "dev.workbench.cleanmixp0.bootstrap.P0LoadingPlugin", build
        )
        self.assertIn("tasks.matching { task -> task.name in ['runServer', 'runClient'] }", build)

    def test_runner_executes_exact_p0_set_with_all_producers(self) -> None:
        source = _read(RUNNER)
        matrix = json.loads(MATRIX.read_bytes())
        p0 = {row["id"] for row in matrix["rows"] if row["priority"] == "P0"}
        for row in p0:
            self.assertIn('"' + row + '"', source)
        for producer in (
            "workbenchCleanMixTraceOutput",
            "workbenchCleanMixComponentOutput",
            "workbenchCleanMixConfigLifecycleOutput",
            "workbenchCleanMixTransformerChainOutput",
            "workbenchCleanMixFinalDefinitionOutput",
        ):
            self.assertIn(producer, source)
        self.assertIn('"runClient" if row.side == "client" else "runServer"', source)
        self.assertIn('"fresh_jvm": True', source)
        self.assertIn('"supported_compatibility_claim": False', source)
        self.assertIn('"unexpected_passes": unexpected_passes', source)

    def test_retained_exact_execution_if_available(self) -> None:
        report_path = (
            ROOT / ".workbench/evidence/cleanmix-runtime-conformance"
            / "x01-execution-v4/execution-report.json"
        )
        if not report_path.is_file():
            self.skipTest("retained ignored X01 execution is unavailable")
        report = json.loads(report_path.read_bytes())
        material = deepcopy(report)
        execution_id = material.pop("execution_id")
        self.assertEqual(
            "cleanmix-p0-runtime-matrix-execution:sha256:"
            + hashlib.sha256(_canonical(material)).hexdigest(),
            execution_id,
        )
        self.assertEqual("complete", report["execution_state"])
        self.assertEqual("review", report["acceptance"]["state"])
        self.assertFalse(report["acceptance"]["supported_compatibility_claim"])
        self.assertEqual(9, report["summary"]["executed_row_count"])
        self.assertEqual(
            {"must_reject": 1, "pass": 5, "xfail": 2, "xpass": 1},
            report["summary"]["dispositions"],
        )
        self.assertTrue(all(row["fresh_jvm"] for row in report["rows"]))
        self.assertEqual(
            ["P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER"],
            report["acceptance"]["unexpected_passes"],
        )


if __name__ == "__main__":
    unittest.main()
