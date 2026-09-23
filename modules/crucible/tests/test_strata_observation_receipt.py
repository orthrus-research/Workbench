from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_strata_observation import (  # noqa: E402
    STRATA_INTERFACE_FILES,
    StrataObservationValidationError,
    build_strata_observation_receipt,
    parse_strata_observation_receipt,
    write_strata_observation_receipt,
)


SCHEMA = ROOT / "modules/crucible/schemas/strata-observation-receipt-v1.schema.json"
TOOL = ROOT / "modules/crucible/tools/run_strata_observation.py"


def load_observation_tool():
    spec = importlib.util.spec_from_file_location("run_strata_observation", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StrataObservationReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workbench = root / "Workbench"
        self.strata = root / "strata"
        self.runtime = self.workbench / ".workbench/runtime"
        self.output = self.workbench / ".workbench/evidence/strata/test"
        self.runtime.mkdir(parents=True)
        self.output.mkdir(parents=True)
        (self.runtime / "mods").mkdir()
        for index, relative in enumerate(STRATA_INTERFACE_FILES):
            self.write(self.strata / relative, f"interface {index}\n")

        self.server_jar = self.write(self.runtime / "cleanroom.jar", b"server")
        self.write(self.runtime / "mods/subject.jar", b"subject")
        self.write(
            self.runtime / "mods/strata-worldgen-observer-0.1.0.jar",
            b"observer",
        )
        self.scan = self.runtime / "strata-worldgen-observer/test.json"
        self.package = self.output / "test.strataview"
        self.manifest = self.output / "test.strataview.d/manifest.json"
        self.report = self.output / "test.capture-report.json"
        self.launch_log = self.write(self.runtime / "logs/strata-scan-test.log", "clean stop\n")
        self.driver_log = self.write(self.output / "capture-driver.log", "validators passed\n")
        self.renderer_log = self.write(self.output / "renderer-build.log", "build passed\n")
        self.write_json(self.scan, self.scan_value())
        self.write_json(self.package, {"schema": "strata.strataview.package.v1"})
        self.write_json(
            self.manifest,
            {"schema": "strata.strataview.region-manifest.v1"},
        )
        self.write_json(self.report, self.report_value())

    @staticmethod
    def write(path: Path, value: str | bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")
        return path

    @classmethod
    def write_json(cls, path: Path, value: dict) -> Path:
        return cls.write(path, json.dumps(value) + "\n")

    @staticmethod
    def scan_value() -> dict:
        return {
            "dimensionId": 0,
            "providerClass": "fixture.WorldProvider",
            "worldSeed": 8675309,
            "terrainType": "wb_proto",
            "chunkGeneratorClass": "fixture.WorldStudioGenerator",
            "chunkWindow": {
                "minChunkX": -2,
                "minChunkZ": -2,
                "chunkSizeX": 2,
                "chunkSizeZ": 2,
                "haloChunks": 1,
            },
            "scannedTerrainPopulatedChunks": 4,
            "voxelMode": "dense",
            "denseBlockMap": {"mode": "dense-section-paletted-states"},
            "denseWorldApiCrossCheck": {"checkedSamples": 1024, "mismatches": 0},
            "worldApiCrossCheck": {"checkedVoxels": 0, "mismatches": 0},
        }

    def report_value(self) -> dict:
        return {
            "paths": {
                "scan": str(self.scan),
                "package": str(self.package),
                "manifest": str(self.manifest),
            },
            "sharded": True,
            "renderSmoke": False,
            "timings": {
                "extractionValidateSeconds": 0.1,
                "packageSeconds": 0.2,
                "validateSeconds": 0.3,
                "shardSeconds": 0.4,
                "shardValidateSeconds": 0.5,
            },
        }

    def build(self) -> dict:
        return build_strata_observation_receipt(
            workbench_root=self.workbench,
            strata_root=self.strata,
            runtime_root=self.runtime,
            server_jar=self.server_jar,
            scan_path=self.scan,
            package_path=self.package,
            manifest_path=self.manifest,
            report_path=self.report,
            launch_log_path=self.launch_log,
            capture_driver_log_path=self.driver_log,
            renderer_build_log_path=self.renderer_log,
            render_smoke=False,
        )

    def test_complete_receipt_binds_external_interface_runtime_and_artifacts(self) -> None:
        receipt = self.build()
        self.assertNotIn("AGENTS.md", STRATA_INTERFACE_FILES)
        self.assertFalse((self.strata / "AGENTS.md").exists())
        self.assertTrue(receipt["receipt_id"].startswith("crucible-strata-observation:sha256:"))
        self.assertEqual(
            len(receipt["adapter"]["strata"]["files"]),
            len(STRATA_INTERFACE_FILES),
        )
        self.assertEqual(receipt["capture"]["terrain_type"], "wb_proto")
        self.assertEqual(receipt["capture"]["terrain_populated_chunks"], 4)
        self.assertEqual(receipt["checks"]["renderer_smoke"], "not_run")
        self.assertIsNone(receipt["artifacts"]["screenshot"])
        self.assertEqual(parse_strata_observation_receipt(receipt), receipt)

        output = self.output / "receipt.json"
        write_strata_observation_receipt(output, receipt)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), receipt)

    def test_incomplete_population_or_dense_mismatch_is_rejected(self) -> None:
        value = self.scan_value()
        value["scannedTerrainPopulatedChunks"] = 3
        self.write_json(self.scan, value)
        with self.assertRaisesRegex(StrataObservationValidationError, "3 of 4"):
            self.build()

        value = self.scan_value()
        value["denseWorldApiCrossCheck"]["mismatches"] = 1
        self.write_json(self.scan, value)
        with self.assertRaisesRegex(StrataObservationValidationError, "mismatches"):
            self.build()

    def test_identity_and_fixed_boundaries_fail_closed(self) -> None:
        receipt = self.build()
        changed = deepcopy(receipt)
        changed["boundaries"]["visual_quality_approved"] = True
        with self.assertRaisesRegex(StrataObservationValidationError, "boundaries drift"):
            parse_strata_observation_receipt(changed)

        changed = deepcopy(receipt)
        changed["capture"]["world_seed"] += 1
        with self.assertRaisesRegex(StrataObservationValidationError, "ID drift"):
            parse_strata_observation_receipt(changed)

    def test_schema_and_cli_are_operable(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(self.build())
        completed = subprocess.run(
            [sys.executable, str(TOOL), "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--strata-root", completed.stdout)
        self.assertIn("--runtime", completed.stdout)

    def test_path_like_executable_is_resolved_before_external_cwd_change(self) -> None:
        tool = load_observation_tool()
        executable = self.workbench / "tools/java"
        self.write(executable, "#!/bin/sh\n")
        executable.chmod(0o755)
        self.assertEqual(
            tool.normalize_executable("tools/java", self.workbench),
            str(executable.resolve()),
        )
        self.assertEqual(tool.normalize_executable("java", self.workbench), "java")

    def test_runtime_progress_filter_keeps_stage_markers_not_minecraft_noise(self) -> None:
        tool = load_observation_tool()
        self.assertTrue(tool.is_progress_line("[dense-capture] package: result"))
        self.assertTrue(tool.is_progress_line("  warning: scan has no fluidCells"))
        self.assertTrue(tool.is_progress_line("BUILD SUCCESSFUL in 4s"))
        self.assertFalse(
            tool.is_progress_line(
                '[10:00:00] WORLDGEN_PROTOTYPE {"event":"chunk.generate"}'
            )
        )


if __name__ == "__main__":
    unittest.main()
