#!/usr/bin/env python3

"""End-to-end active-instance registration wizard tests."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlparse


MODULE_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.active_instance import (  # noqa: E402
    ActiveInstanceError,
    initialize_active_instance,
    load_active_instance,
)
from workbench_shell.cli import main as cli_main  # noqa: E402
from workbench_core.configuration import load_workbench_configuration  # noqa: E402
from workbench_shell.registration_wizard import (  # noqa: E402
    RegistrationWizardError,
    apply_active_registration,
    plan_active_registration,
    registration_capabilities,
)


PACK_TOML = """\
name = "Supersymmetry"
author = "SymmetricDevs"
version = "test"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

[versions]
forge = "14.23.5.2860"
minecraft = "1.12.2"
"""


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _project(parent: Path) -> Path:
    root = parent / "pack"
    root.mkdir()
    for directory in ("config", "groovy", "mods"):
        (root / directory).mkdir()
    (root / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
    (root / "index.toml").write_text("", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Workbench Test")
    _git(root, "config", "user.email", "workbench@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root


def _instance(parent: Path, payload_name: str = ".minecraft") -> tuple[Path, Path]:
    platform = load_workbench_configuration(SUITE_ROOT).platform_document.values
    instance = parent / "instance"
    payload = instance / payload_name
    for directory in ("config", "groovy", "mods"):
        (payload / directory).mkdir(parents=True, exist_ok=True)
    (instance / "mmc-pack.json").write_text(json.dumps({
        "formatVersion": 1,
        "components": [
            {
                "uid": "net.minecraft",
                "version": "1.12.2",
                "cachedName": "Minecraft",
            },
            {
                "uid": "net.minecraftforge",
                "version": platform["cleanroom_version"],
                "cachedName": "Cleanroom",
            },
        ],
    }), encoding="utf-8")
    (instance / "instance.cfg").write_text(
        "name=Workbench Active Test\n"
        "ManagedPackID=Supersymmetry\n"
        "lastLaunchTime=999999\n",
        encoding="utf-8",
    )
    for name in (
        "gregtech-2.8.10-beta.jar",
        "groovyscript-1.2.0.jar",
        "supersymmetry-v0.1.111.jar",
    ):
        (payload / "mods" / name).write_bytes((name + "\n").encode("ascii"))

    material = payload / "groovy/material"
    material.mkdir(parents=True)
    (material / "SuSyMaterials.groovy").write_text(
        "package material\n\n"
        "class SuSyMaterials {\n\n"
        "    // Petrochem Materials\n\n"
        "    public static Material ExistingFluid\n\n"
        "    // First Degree Materials A\n"
        "}\n",
        encoding="utf-8",
    )
    (material / "PetrochemistryMaterials.groovy").write_text(
        "package material\n\n"
        "import static material.SuSyMaterials.*\n\n"
        "class PetrochemistryMaterials {\n\n"
        "    static void register() {\n\n"
        "        ExistingFluid = new Material.Builder(20000, "
        "SuSyUtility.susyId('existing_fluid'))\n"
        "                .liquid()\n"
        "                .color(0x111111)\n"
        "                .flags(FLAMMABLE)\n"
        "                .build()\n"
        "    }\n"
        "}",
        encoding="utf-8",
    )
    language = payload / "resources/langfiles/lang/en_us.lang"
    language.parent.mkdir(parents=True)
    language.write_text(
        "# Fluids\n\n"
        "susy.material.existing_fluid=Existing Fluid\n"
        "\n# Thermodynamics\n",
        encoding="utf-8",
    )

    prepost = payload / "groovy/prePostInit"
    prepost.mkdir(parents=True)
    (prepost / "Recipemaps.groovy").write_text(
        "package prePostInit\n\n"
        "class Recipemaps {\n"
        "    static final def MIXER = recipemap('mixer')\n"
        "}\n",
        encoding="utf-8",
    )
    (prepost / "oreDict.groovy").write_text(
        "package prePostInit;\n\n"
        "ore('dustExisting').add(metaitem('dustExisting'))\n",
        encoding="utf-8",
    )
    script = payload / "groovy/postInit/chemistry/Probe.groovy"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import static prePostInit.Recipemaps.*\n"
        "import static gregtech.api.GTValues.*\n\n"
        "MIXER.recipeBuilder()\n"
        "    .fluidInputs(fluid('water') * 1000)\n"
        "    .fluidOutputs(fluid('distilled_water') * 1000)\n"
        "    .duration(20)\n"
        "    .EUt(VA[LV])\n"
        "    .buildAndRegister()\n",
        encoding="utf-8",
    )
    return instance, payload


def _uri_path(value: str) -> Path:
    parsed = urlparse(value)
    return Path(unquote(parsed.path))


class RegistrationWizardTest(unittest.TestCase):

    def test_all_ready_patterns_apply_directly_and_retain_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            selected = initialize_active_instance(
                SUITE_ROOT,
                project,
                instance,
                state_root=state,
            )

            self.assertEqual(selected["outcome"], "selected")
            self.assertEqual(
                load_active_instance(
                    SUITE_ROOT, project, state_root=state
                )["payload_path"],
                payload.resolve(),
            )
            capabilities = registration_capabilities(
                SUITE_ROOT, project, state_root=state
            )
            self.assertEqual(len(capabilities["families"]), 36)
            self.assertEqual(
                {row["key"] for row in capabilities["patterns"]},
                {
                    "material-backed-fluid",
                    "machine-recipe",
                    "ore-dictionary-entry",
                },
            )
            self.assertEqual(
                capabilities["runtime_options"]["machine-recipe"]["recipe_map"],
                ["MIXER"],
            )

            recipe_answers = {
                "script": "groovy/postInit/chemistry/Probe.groovy",
                "recipe_map": "MIXER",
                "item_inputs": [
                    {"kind": "ore", "name": "dustSulfur", "amount": 1}
                ],
                "fluid_inputs": [],
                "item_outputs": [],
                "fluid_outputs": [{"name": "sulfuric_water", "amount": 1000}],
                "duration": 100,
                "voltage_tier": "LV",
            }
            script = payload / "groovy/postInit/chemistry/Probe.groovy"
            before_recipe = script.read_bytes()
            plan = plan_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="machine-recipe",
                answers=recipe_answers,
                state_root=state,
            )
            self.assertEqual(script.read_bytes(), before_recipe)
            self.assertIn("MIXER.recipeBuilder()", plan["operations"][0]["diff"])
            with self.assertRaisesRegex(
                RegistrationWizardError,
                "plan changed after review",
            ):
                apply_active_registration(
                    SUITE_ROOT,
                    project,
                    pattern_key="machine-recipe",
                    answers=recipe_answers,
                    expected_plan_id="sha256:" + ("0" * 64),
                    state_root=state,
                )
            self.assertEqual(script.read_bytes(), before_recipe)
            recipe_result = apply_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="machine-recipe",
                answers=recipe_answers,
                expected_plan_id=plan["plan_id"],
                state_root=state,
            )
            self.assertIn("fluid('sulfuric_water')", script.read_text("utf-8"))
            receipt_path = _uri_path(
                recipe_result["receipt"]["target"]["receipt_uri"]
            )
            backup = (
                receipt_path.parent
                / recipe_result["receipt"]["outputs"][0]["backup_path"]
            )
            self.assertEqual(backup.read_bytes(), before_recipe)

            ore_result = apply_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="ore-dictionary-entry",
                answers={
                    "ore_name": "dustWorkbenchProbe",
                    "ingredient": {
                        "kind": "metaitem",
                        "name": "dustSodiumHydroxide",
                    },
                },
                state_root=state,
            )
            self.assertEqual(ore_result["outcome"], "applied")
            self.assertIn(
                "ore('dustWorkbenchProbe').add("
                "metaitem('dustSodiumHydroxide'))",
                (payload / "groovy/prePostInit/oreDict.groovy").read_text("utf-8"),
            )

            material_result = apply_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="material-backed-fluid",
                answers={"name": "Pilot Coolant", "color": "0x425d73"},
                state_root=state,
            )
            self.assertEqual(
                material_result["plan"]["effective_answers"]["material_id"],
                20001,
            )
            self.assertIn(
                "PilotCoolant = new Material.Builder(20001, "
                "SuSyUtility.susyId('pilot_coolant'))",
                (payload / "groovy/material/PetrochemistryMaterials.groovy").read_text(
                    "utf-8"
                ),
            )
            self.assertFalse((payload / "groovy/preInit/register_material_pilot_coolant.groovy").exists())
            self.assertEqual(
                load_active_instance(
                    SUITE_ROOT, project, state_root=state
                )["selection_id"],
                selected["selection"]["selection_id"],
            )

    def test_runtime_marker_drift_requires_reinitialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root, "minecraft")
            state = root / "state"
            initialize_active_instance(
                SUITE_ROOT, project, payload, state_root=state
            )
            (payload / "mods/gregtech-2.8.10-beta.jar").write_bytes(b"drift\n")

            with self.assertRaisesRegex(ActiveInstanceError, "identity has drifted"):
                load_active_instance(SUITE_ROOT, project, state_root=state)

    def test_cli_initializes_and_applies_json_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main([
                    "initialize",
                    str(project),
                    "--instance",
                    str(instance),
                    "--suite-root",
                    str(SUITE_ROOT),
                    "--state-root",
                    str(state),
                    "--json",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["outcome"], "selected")

            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main([
                    "register",
                    str(project),
                    "--list",
                    "--suite-root",
                    str(SUITE_ROOT),
                    "--state-root",
                    str(state),
                    "--json",
                ])
            self.assertEqual(status, 0)
            listed = json.loads(output.getvalue())
            self.assertEqual(len(listed["families"]), 36)
            self.assertEqual(len(listed["patterns"]), 3)

            answers = root / "answers.json"
            answers.write_text(json.dumps({
                "ore_name": "dustCliProbe",
                "ingredient": {
                    "kind": "metaitem",
                    "name": "dustSodiumHydroxide",
                },
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main([
                    "register",
                    str(project),
                    "--pattern",
                    "ore-dictionary-entry",
                    "--answers",
                    str(answers),
                    "--apply",
                    "--yes",
                    "--suite-root",
                    str(SUITE_ROOT),
                    "--state-root",
                    str(state),
                    "--json",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["outcome"], "applied")
            self.assertIn(
                "ore('dustCliProbe')",
                (payload / "groovy/prePostInit/oreDict.groovy").read_text("utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
