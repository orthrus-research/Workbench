#!/usr/bin/env python3

"""Focused conformance for the profile-owned experimental material pattern."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _support import LEDGER_PATH, SOURCE_ROOT, WORKBENCH_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from workbench_blueprints import planner  # noqa: E402
from workbench_blueprints import standards  # noqa: E402


EXPERIMENTAL_ROOT = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/blueprints/experimental/standards"
)
PACK_REVISION = "9d3aa7ae0294bf27f0b8acbb893d61da23a06972"
MATERIAL_QUERY = "CIT-PACK-DILUTED-OIL-MATERIAL"
MATERIAL_RESULT = f"SRC-PACK@{PACK_REVISION}:{MATERIAL_QUERY}"
LOCALIZATION_QUERY = (
    f"SRC-PACK@{PACK_REVISION}:resources/langfiles/lang/en_us.lang"
    "#susy.material.diluted_oil_light"
)
LOCALIZATION_RESULT = (
    "source-file:sha256:"
    "d23c3107bf54a6a25f59a0f129fb8135e2e71dcc7b08f569fbc6c6db5009cb0a"
)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class ExperimentalSupersymmetryPatternTest(unittest.TestCase):

    def test_registry_compiles_and_planner_emits_deterministic_ready_candidate(
        self,
    ) -> None:
        compiled_registry = standards.compile_registry(
            EXPERIMENTAL_ROOT, asset_root=WORKBENCH_ROOT
        )
        locked_registry = standards.check_registry(
            EXPERIMENTAL_ROOT, asset_root=WORKBENCH_ROOT
        )
        self.assertEqual(compiled_registry, locked_registry)
        self.assertEqual(len(locked_registry["standards"]), 1)

        compiled, _source = standards.compile_file(
            EXPERIMENTAL_ROOT / "material-backed-fluid.yaml",
            registry_root=EXPERIMENTAL_ROOT,
            asset_root=WORKBENCH_ROOT,
        )
        self.assertEqual(
            compiled["atlas"]["baseline_id"], f"SRC-PACK@{PACK_REVISION}"
        )
        self.assertNotIn("synthetic", standards.canonical_json(compiled).lower())
        self.assertEqual(compiled["validation"]["gates"], [])
        self.assertIn(
            "runtime success is unproven",
            compiled["diagnostics"][0]["template"],
        )

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            repository = root / "pack"
            repository.mkdir()
            baseline_files = {
                "groovy/preInit/MaterialChanges.groovy": (
                    "material.SuSyMaterials.init()\n"
                ),
                "groovy/material/PetrochemistryMaterials.groovy": (
                    "DilutedOilLight = new Material.Builder(20000, "
                    "SuSyUtility.susyId('diluted_oil_light'))\n"
                    "        .liquid()\n"
                    "        .color(0x2d2f3b)\n"
                    "        .flags(FLAMMABLE)\n"
                    "        .build()\n"
                ),
                "resources/langfiles/lang/en_us.lang": (
                    "susy.material.diluted_oil_light=Diluted Light Oil\n"
                ),
                "resources/pack.mcmeta": (
                    '{"pack":{"pack_format":3,"description":"Supersymmetry"}}\n'
                ),
            }
            for relative, content in baseline_files.items():
                target_file = repository / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")
            _git(repository, "init", "-q")
            _git(repository, "config", "user.email", "blueprints@example.invalid")
            _git(repository, "config", "user.name", "Blueprints Test")
            _git(repository, "add", ".")
            _git(repository, "commit", "-q", "-m", "pinned pack-shaped baseline")

            target = planner.capture_target_state(repository, "pack")
            evidence = planner.build_planning_evidence(
                target["target_state_id"],
                queries=[
                    {
                        "standard_key": "material-backed-fluid",
                        "query_id": MATERIAL_QUERY,
                        "result_id": MATERIAL_RESULT,
                        "availability": "available",
                        "result": {
                            "source": {
                                "revision": PACK_REVISION,
                                "path": (
                                    "groovy/material/"
                                    "PetrochemistryMaterials.groovy"
                                ),
                                "file_sha256": (
                                    "29c2813429442300e62c20e66c398811"
                                    "7d5ce9e3b4124d387de4db3f4002661b"
                                ),
                            },
                            "registration": {
                                "material_id": 20000,
                                "registry_name": "diluted_oil_light",
                                "namespace_helper": "SuSyUtility.susyId",
                                "form": "liquid",
                            },
                        },
                    },
                    {
                        "standard_key": "material-backed-fluid",
                        "query_id": LOCALIZATION_QUERY,
                        "result_id": LOCALIZATION_RESULT,
                        "availability": "available",
                        "result": {
                            "source": {
                                "revision": PACK_REVISION,
                                "path": "resources/langfiles/lang/en_us.lang",
                                "file_sha256": LOCALIZATION_RESULT.split(":")[-1],
                            },
                            "localization": {
                                "key": "susy.material.diluted_oil_light",
                                "value": "Diluted Light Oil",
                            },
                        },
                    },
                ],
                allocation=[
                    {
                        "domain_name": "supersymmetry-material-id-experimental",
                        "authority_id": "blueprints-allocation-ledger-v1",
                        "occupied_values": [
                            20000,
                            20001,
                            20002,
                            20003,
                            20004,
                            20005,
                            20006,
                            20007,
                            20009,
                        ],
                        "proposed_value": None,
                    }
                ],
                reconciliation=[
                    {
                        "standard_key": "material-backed-fluid",
                        "identity_kind": "identity-absent",
                        "observed": None,
                    }
                ],
            )
            engine = planner.Planner(
                registry_root=EXPERIMENTAL_ROOT,
                asset_root=WORKBENCH_ROOT,
                ledger_path=LEDGER_PATH,
                target_repository=repository,
                sealed_store=planner.SealedStore(root / "sealed"),
            )
            intake = {
                "sequence": 0,
                "feature_family": "material-backed-fluid",
                "intent": {
                    "operation": "create",
                    "target_key": "susy:pilot_coolant",
                    "desired_outcome": (
                        "Plan one experimental Supersymmetry material-backed fluid."
                    ),
                },
                "parameters": [
                    {"name": "translation", "value": "Pilot Coolant"},
                    {"name": "color", "value": "0x425d73"},
                    {"name": "name", "value": "Pilot Coolant"},
                ],
                "requested_variants": [],
                "output_mode": "instructions",
                "consent": {
                    "accept_compliant_revision": False,
                    "allow_direct_apply": False,
                },
            }

            first = engine.execute(intake, target, evidence)
            second = engine.execute(intake, target, evidence)
            self.assertEqual(first, second)
            self.assertEqual(first["diagnostics"], [])
            self.assertEqual(first["plan"]["status"], "ready")
            self.assertIsNotNone(first["candidate"])
            self.assertEqual(
                first["plan"]["selection"]["variant_ids"],
                ["resource-loader-localization"],
            )
            self.assertEqual(
                [row["path"] for row in first["plan"]["operations"]],
                [
                    "groovy/preInit/register_material_pilot_coolant.groovy",
                    "resources/susy_blueprint_pilot_coolant/lang/en_us.lang",
                ],
            )
            parameters = {
                row["name"]: row for row in first["plan"]["effective_parameters"]
            }
            self.assertEqual(parameters["material_id"]["value"], 20008)
            self.assertEqual(parameters["registry_name"]["value"], "pilot_coolant")
            self.assertFalse(
                any(
                    stage["kind"] in {"runtime", "presentation", "integration"}
                    for stage in first["plan"]["validation_stages"]
                )
            )

            sealed = engine.sealed_store.read(
                first["candidate"]["sealed_locator"]
            )
            rendered = {
                row["path"]: base64.b64decode(row["content_base64"]).decode(
                    "utf-8"
                )
                for row in sealed["operations"]
            }
            self.assertIn(
                "new Material.Builder(20008, "
                "SuSyUtility.susyId('pilot_coolant'))",
                rendered[
                    "groovy/preInit/register_material_pilot_coolant.groovy"
                ],
            )
            self.assertIn(
                "eventManager.listen {",
                rendered[
                    "groovy/preInit/register_material_pilot_coolant.groovy"
                ],
            )
            self.assertIn(
                "MaterialEvent event ->",
                rendered[
                    "groovy/preInit/register_material_pilot_coolant.groovy"
                ],
            )
            self.assertEqual(
                rendered[
                    "resources/susy_blueprint_pilot_coolant/lang/en_us.lang"
                ],
                "susy.material.pilot_coolant=Pilot Coolant\n",
            )


if __name__ == "__main__":
    unittest.main()
