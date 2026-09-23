"""Focused tests for retained world-generation evidence auditing."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from supersymmetry_project_fixture import (  # noqa: E402
    create_supersymmetry_project,
)
from workbench_shell.runtime_worldgen_audit import (  # noqa: E402
    RuntimeWorldgenAuditError,
    audit_project_worldgen,
)


def _write_biome(
    root: Path,
    filename: str,
    resource: str,
    dictionary: list[str],
    entries: dict[str, list[int]],
) -> None:
    payload = {
        "Resource Location": resource,
        "Dictionary Types": dictionary,
        "BiomeManager Entries": {
            f"{climate} Weights": weights
            for climate, weights in entries.items()
        },
    }
    (root / filename).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _minimal_class(*utf8_constants: str) -> bytes:
    constant_pool = b"".join(
        b"\x01" + len(value.encode("utf-8")).to_bytes(2, "big")
        + value.encode("utf-8")
        for value in utf8_constants
    )
    return (
        b"\xca\xfe\xba\xbe"
        + b"\x00\x00\x00\x34"
        + (len(utf8_constants) + 1).to_bytes(2, "big")
        + constant_pool
    )


def _runtime(parent: Path) -> Path:
    runtime = parent / "runtime"
    scripts = runtime / "config/BiomeTweaker/scripts"
    reports = runtime / "config/BiomeTweaker/output/biome"
    rtg_config = runtime / "config/RTG"
    logs = runtime / "logs"
    mods = runtime / "mods"
    for directory in (scripts, reports, rtg_config, logs, mods):
        directory.mkdir(parents=True, exist_ok=True)
    (scripts / "RTG.cfg").write_text(
        """\
vanillaWarmOverworldBiomes = forAllBiomesExcept(vanillaPlains, vanillaForest)
vanillaIcyOverworldBiomes = forBiomes(vanillaIcePlains)
vanillaWarmOverworldBiomes.addToGeneration("WARM", 1)
vanillaIcyOverworldBiomes.addToGeneration("ICY", 1)
""",
        encoding="utf-8",
    )
    _write_biome(
        reports,
        "Hell (minecraft_hell).json",
        "minecraft:hell",
        ["NETHER", "HOT"],
        {"WARM": [1]},
    )
    _write_biome(
        reports,
        "Lunar Highlands (susy_moon).json",
        "susy:moon",
        ["COLD", "DRY"],
        {"WARM": [1], "COOL": [0]},
    )
    _write_biome(
        reports,
        "Alps (biomesoplenty_alps).json",
        "biomesoplenty:alps",
        ["COLD", "SNOWY"],
        {"WARM": [5, 5], "ICY": [5]},
    )
    (logs / "latest.log").write_text(
        """\
[01:00:00] [Server thread/WARN] [RTG]: || RTG could not find realistic versions of the following biomes ||
[01:00:00] [Server thread/WARN] [RTG]: || 114 | BiomeLunarHighlands | susy:moon ||
[01:00:00] [Server thread/WARN] [RTG]: `= === | === | === =`
[01:00:01] [Server thread/FATAL] [dimstack]: The chunk generator rtg.world.gen.ChunkGeneratorRTG did not trigger net.minecraftforge.event.terraingen.InitNoiseGensEvent during initialization!
[01:00:01] [Server thread/WARN] [dimstack]: Could not provide native noise fields and RNG for dimension id 0: overworld!
Terrain features that depend on these won't work correctly.
[01:00:02] [Server thread/INFO] [RTG]: World Seed: -42
[01:00:03] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer]: Done (2.000s)! For help, type "help" or "?"
""",
        encoding="utf-8",
    )
    (rtg_config / "rtg.cfg").write_text(
        """\
general {
    B:oreGenEventCancellation=true
    B:rtgWorldTypeByDefault=true
    B:activeSetting=true
    S:patchBiome=minecraft:plains
}
""",
        encoding="utf-8",
    )
    with zipfile.ZipFile(mods / "RTGU-1.12.2-test.jar", "w") as archive:
        archive.writestr(
            "rtg/RTGConfig$Setting.class",
            _minimal_class("activeSetting", "patchBiome"),
        )
    return runtime


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class RuntimeWorldgenAuditTest(unittest.TestCase):
    def test_joins_static_registry_log_and_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            runtime = _runtime(root)
            before = _tree_bytes(runtime)

            first = audit_project_worldgen(
                REPOSITORY_ROOT,
                project,
                runtime_root=runtime,
            )
            second = audit_project_worldgen(
                REPOSITORY_ROOT,
                project,
                runtime_root=runtime,
            )

            self.assertEqual(first, second)
            self.assertEqual(first["state"], "findings-observed")
            self.assertEqual(
                first["profile_expectations"][0]["state"],
                "mismatched",
            )
            self.assertEqual(first["operation_class"], "read-only")
            self.assertEqual(first["authority"], {
                "classification": "atlas-derived-observation",
                "normative": False,
                "atlas_publication": False,
                "sentinel_policy_finding": False,
            })
            self.assertEqual(len(first["evidence"]), 7)
            self.assertEqual(
                first["biome_registry"]["report_count"],
                3,
            )
            warm = next(
                item for item in first["biome_registry"]["climate_totals"]
                if item["climate"] == "WARM"
            )
            self.assertEqual(warm, {
                "climate": "WARM",
                "biome_count": 3,
                "entry_count": 4,
            })
            self.assertNotIn(
                "susy:moon",
                {
                    item["resource_location"]
                    for item in first["biome_registry"][
                        "cross_climate_memberships"
                    ]
                },
            )
            categories = {
                finding["category"] for finding in first["findings"]
            }
            self.assertEqual(categories, {
                "generation-selector-mismatch",
                "duplicate-biome-manager-membership",
                "cross-climate-biome-manager-membership",
                "non-overworld-biome-climate-membership",
                "rtg-unsupported-biome-climate-overlap",
                "reported-missing-worldgen-event",
                "configuration-key-class-literal-not-observed",
            })
            selector_finding = next(
                finding for finding in first["findings"]
                if finding["category"] == "generation-selector-mismatch"
            )
            self.assertEqual(selector_finding["confidence"], "observed")
            self.assertNotIn("guidance", selector_finding)
            self.assertEqual(
                selector_finding["facts"]["profile_artifact_binding"][
                    "state"
                ],
                "unverified",
            )
            overlap = next(
                finding for finding in first["findings"]
                if finding["category"]
                == "rtg-unsupported-biome-climate-overlap"
            )
            self.assertEqual(len(overlap["evidence_ids"]), 3)
            self.assertEqual(
                overlap["facts"]["configured_patch_biome"],
                "minecraft:plains",
            )
            self.assertEqual(
                first["log_observations"]["world_seeds"][0]["seed"],
                -42,
            )
            event_finding = next(
                finding for finding in first["findings"]
                if finding["category"] == "reported-missing-worldgen-event"
            )
            self.assertEqual(
                event_finding["facts"]["later_server_ready_line"],
                8,
            )
            self.assertTrue(
                event_finding["facts"]["associated_noise_state_warnings"]
                [0]["consequence_observed"]
            )
            self.assertEqual(
                {
                    item["key"]: item["state"]
                    for item in first["configuration_observations"]
                },
                {
                    "oreGenEventCancellation": "class-literal-not-observed",
                    "patchBiome": "class-literal-observed",
                    "rtgWorldTypeByDefault": "class-literal-not-observed",
                },
            )
            self.assertEqual(before, _tree_bytes(runtime))

            schema = json.loads(
                (
                    MODULE_ROOT
                    / "schemas/runtime-worldgen-audit-v1.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(first)

    def test_surfaces_missing_optional_evidence_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            runtime = root / "empty-runtime"
            runtime.mkdir()

            result = audit_project_worldgen(
                REPOSITORY_ROOT,
                project,
                runtime_root=runtime,
            )

            self.assertEqual(result["state"], "insufficient-evidence")
            self.assertEqual(result["evidence"], [])
            self.assertEqual(result["biome_registry"]["state"], "unavailable")
            self.assertEqual(result["log_observations"]["state"], "unavailable")
            self.assertEqual(
                result["profile_expectations"][0]["state"],
                "unobserved",
            )
            self.assertTrue(any(
                "No BiomeTweaker biome reports" in limitation
                for limitation in result["limitations"]
            ))
            self.assertEqual(list(runtime.iterdir()), [])

    def test_rejects_malformed_runtime_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            runtime = root / "runtime"
            reports = runtime / "config/BiomeTweaker/output/biome"
            reports.mkdir(parents=True)
            (reports / "broken.json").write_text("{\n", encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeWorldgenAuditError,
                "invalid JSON",
            ):
                audit_project_worldgen(
                    REPOSITORY_ROOT,
                    project,
                    runtime_root=runtime,
                )

    def test_rejects_negative_biome_weight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            runtime = root / "runtime"
            reports = runtime / "config/BiomeTweaker/output/biome"
            reports.mkdir(parents=True)
            _write_biome(
                reports,
                "invalid.json",
                "example:invalid",
                [],
                {"WARM": [-1]},
            )

            with self.assertRaisesRegex(
                RuntimeWorldgenAuditError,
                "weights are invalid",
            ):
                audit_project_worldgen(
                    REPOSITORY_ROOT,
                    project,
                    runtime_root=runtime,
                )

    def test_rejects_symbolic_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            runtime = root / "runtime"
            runtime.mkdir()
            link = root / "runtime-link"
            link.symlink_to(runtime, target_is_directory=True)

            with self.assertRaisesRegex(
                RuntimeWorldgenAuditError,
                "symbolic link",
            ):
                audit_project_worldgen(
                    REPOSITORY_ROOT,
                    project,
                    runtime_root=link,
                )


if __name__ == "__main__":
    unittest.main()
