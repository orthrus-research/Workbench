from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_strata_micro_region import (  # noqa: E402
    StrataMicroRegionValidationError,
    build_strata_micro_region_receipt,
    parse_strata_micro_region_receipt,
)
from workbench_crucible_strata_micro_region.observation import (  # noqa: E402
    _BASE_INTERFACE_FILES,
)


class StrataMicroRegionReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workbench = root / "Workbench"
        self.strata = root / "strata"
        self.runtime = self.workbench / ".workbench/runtime"
        self.output = self.workbench / ".workbench/evidence/micro"
        (self.runtime / "mods").mkdir(parents=True)
        self.output.mkdir(parents=True)
        for index, relative in enumerate(_BASE_INTERFACE_FILES):
            self.write(self.strata / relative, f"interface {index}\n")

        self.server = self.write(self.runtime / "cleanroom.jar", b"server")
        self.write(self.runtime / "mods/subject.jar", b"subject")
        observer = self.runtime / "mods/strata-worldgen-observer-0.2.0.jar"
        with ZipFile(observer, "w") as archive:
            archive.writestr("fixture/Observer.class", b"\xca\xfe\xba\xbe\x00\x00\x004")
            archive.writestr(
                "META-INF/MANIFEST.MF",
                "Manifest-Version: 1.0\nBuild-Compiler-Java: 25\nBuild-Classfile-Target: 8\n\n",
            )

        self.runtime_jdk = self.jdk(root / "runtime-jdk", "25.0.4", "Eclipse Adoptium")
        self.host_jdk = self.jdk(root / "host-jdk", "21.0.8", "Azul Systems")
        self.compiler_jdk = self.jdk(root / "compiler-jdk", "25.0.4", "Eclipse Adoptium")
        self.scan = self.runtime / "strata-worldgen-observer/micro.json"
        self.package = self.output / "micro.strataview"
        self.manifest = self.output / "micro.strataview.d/manifest.json"
        self.report = self.output / "micro.capture-report.json"
        self.launch_log = self.write(self.runtime / "logs/strata-scan-micro.log", "stopped\n")
        self.driver_log = self.write(self.output / "capture-driver.log", "pass\n")
        self.renderer_log = self.write(self.output / "renderer-build.log", "pass\n")
        self.overview = self.write(self.output / "overview.png", b"overview")
        self.exact = self.write(self.output / "exact.png", b"exact")
        self.write_json(self.scan, self.scan_value())
        self.write_json(self.package, {"schema": "strata.strataview.package.v1"})
        chunks = [
            {"chunkX": index % 16, "chunkZ": index // 16, "values": [64] * 256}
            for index in range(256)
        ]
        biomes = [
            {
                "chunkX": index % 16,
                "chunkZ": index // 16,
                "biomePalette": ["1:minecraft:plains"],
                "biomeIds": [1] * 256,
            }
            for index in range(256)
        ]
        self.write_json(
            self.manifest,
            {
                "schema": "strata.strataview.region-manifest.v2",
                "surfacePreview": {
                    "mode": "derived-landform-and-exact-surface-v1",
                    "heightmaps": {"chunks": chunks},
                    "exactHeightmaps": {"chunks": chunks},
                    "biomeMap": {"chunks": biomes},
                },
            },
        )
        self.write_json(self.manifest.parent / "tiles/tile.json", {"schema": "strata.strataview.tile.v2"})
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
    def jdk(path: Path, version: str, implementor: str) -> Path:
        path.mkdir(parents=True)
        (path / "release").write_text(
            f'JAVA_VERSION="{version}"\nIMPLEMENTOR="{implementor}"\n',
            encoding="utf-8",
        )
        return path

    @staticmethod
    def scan_value() -> dict:
        return {
            "dimensionId": 0,
            "providerClass": "fixture.Provider",
            "worldSeed": 8675309,
            "terrainType": "wb_proto",
            "chunkGeneratorClass": "fixture.Generator",
            "chunkWindow": {
                "minChunkX": 0,
                "minChunkZ": 0,
                "chunkSizeX": 16,
                "chunkSizeZ": 16,
                "haloChunks": 1,
            },
            "scannedTerrainPopulatedChunks": 256,
            "voxelMode": "dense",
            "denseBlockMap": {"mode": "dense-section-paletted-states"},
            "denseWorldApiCrossCheck": {"checkedSamples": 65536, "mismatches": 0},
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
            "renderSmoke": True,
            "manifestVersion": 2,
            "timings": {
                "extractionValidateSeconds": 0.1,
                "packageSeconds": 0.2,
                "validateSeconds": 0.3,
                "shardSeconds": 0.4,
                "shardValidateSeconds": 0.5,
                "renderSmokeSeconds": 0.6,
            },
        }

    def build(self) -> dict:
        return build_strata_micro_region_receipt(
            workbench_root=self.workbench,
            strata_root=self.strata,
            runtime_root=self.runtime,
            server_jar=self.server,
            runtime_java_home=self.runtime_jdk,
            build_java_home=self.host_jdk,
            compiler_java_home=self.compiler_jdk,
            scan_path=self.scan,
            package_path=self.package,
            manifest_path=self.manifest,
            report_path=self.report,
            launch_log_path=self.launch_log,
            capture_driver_log_path=self.driver_log,
            renderer_build_log_path=self.renderer_log,
            overview_screenshot_path=self.overview,
            exact_tile_screenshot_path=self.exact,
        )

    def test_receipt_binds_micro_region_shards_visuals_and_toolchains(self) -> None:
        receipt = self.build()
        self.assertNotIn("AGENTS.md", _BASE_INTERFACE_FILES)
        self.assertFalse((self.strata / "AGENTS.md").exists())
        self.assertEqual(receipt["capture"]["sample_chunks"], 256)
        self.assertEqual(receipt["toolchains"]["runtime"]["java_version"], "25.0.4")
        self.assertEqual(receipt["toolchains"]["build_host"]["java_version"], "21.0.8")
        self.assertEqual(receipt["toolchains"]["observer_artifact"]["classfile_majors"], [52])
        self.assertEqual(receipt["artifacts"]["shards"]["file_count"], 2)
        self.assertEqual(parse_strata_micro_region_receipt(receipt), receipt)

    def test_receipt_identity_drift_fails_closed(self) -> None:
        receipt = self.build()
        changed = deepcopy(receipt)
        changed["capture"]["sample_chunks"] = 255
        with self.assertRaisesRegex(StrataMicroRegionValidationError, "ID drift"):
            parse_strata_micro_region_receipt(changed)


if __name__ == "__main__":
    unittest.main()
