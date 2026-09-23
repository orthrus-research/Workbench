"""Fast smoke tests for the commands a developer reaches first."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "tools/workbench.py"
PACK_TOML = """\
name = "Supersymmetry"
author = "SymmetricDevs"
version = "smoke"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

[versions]
forge = "14.23.5.2860"
minecraft = "1.12.2"
"""


def _run_json(*arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(WORKBENCH), *arguments],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise AssertionError(
            f"workbench {' '.join(arguments)} failed:\n{completed.stderr}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise AssertionError("Workbench JSON output is not an object")
    return value


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _feature_checkout(parent: Path) -> Path:
    project = parent / "supersymmetry-feature"
    project.mkdir()
    for directory in ("config", "mods"):
        (project / directory).mkdir()
    sources = {
        "pack.toml": PACK_TOML,
        "index.toml": "",
        "groovy/preInit/MaterialChanges.groovy": (
            "class MaterialChanges {\n"
            "    static void init() { SuSyMaterials.init() }\n"
            "}\n"
        ),
        "groovy/material/SuSyMaterials.groovy": (
            "package material\n\n"
            "class SuSyMaterials {\n\n"
            "    // Petrochem Materials\n\n"
            "    public static Material ExistingFluid\n\n"
            "    static void init() {\n"
            "        PetrochemistryMaterials.register()\n"
            "    }\n\n"
            "    // First Degree Materials A\n"
            "}\n"
        ),
        "groovy/material/PetrochemistryMaterials.groovy": (
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
            "}\n"
        ),
        "groovy/prePostInit/Recipemaps.groovy": (
            "class Recipemaps {\n"
            "    static final def MIXER = recipemap('mixer')\n"
            "}\n"
        ),
        "groovy/postInit/chemistry/Probe.groovy": (
            "import static prePostInit.Recipemaps.*\n"
            "import static gregtech.api.GTValues.*\n\n"
            "MIXER.recipeBuilder()\n"
            "    .fluidInputs(fluid('water') * 1000)\n"
            "    .fluidOutputs(fluid('distilled_water') * 1000)\n"
            "    .duration(20)\n"
            "    .EUt(VA[LV])\n"
            "    .buildAndRegister()\n"
        ),
        "resources/langfiles/lang/en_us.lang": (
            "# Fluids\n\n"
            "susy.material.existing_fluid=Existing Fluid\n\n"
            "# Thermodynamics\n"
        ),
    }
    for relative, text in sources.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "config", "user.name", "Workbench Smoke")
    _git(project, "config", "user.email", "workbench@example.invalid")
    _git(project, "add", "--all")
    _git(project, "commit", "--quiet", "-m", "feature fixture")
    return project


class ProductSmokeTests(unittest.TestCase):
    def test_source_checkout_reports_usable_core_identity(self) -> None:
        result = _run_json("--version", "--json")

        self.assertEqual("workbench-core", result["component_id"])
        core_version = tomllib.loads((ROOT / "core/pyproject.toml").read_text())["project"]["version"]
        self.assertEqual(core_version, result["version"])
        self.assertEqual({"component_id", "version"}, set(result))

    def test_console_catalog_exposes_the_first_developer_flows(self) -> None:
        result = _run_json("console", "catalog", "--json")
        command_ids = {
            row["command_id"]
            for row in result["commands"]
            if isinstance(row, dict) and isinstance(row.get("command_id"), str)
        }

        self.assertIn("shell.material-fluid-plan", command_ids)
        self.assertIn("workspace.open", command_ids)
        self.assertIn("doctor.inspect", command_ids)
        self.assertIn("developer-features.records", command_ids)
        self.assertIn("developer-features.transaction", command_ids)
        self.assertIn("atlas.recipes-index", command_ids)
        self.assertIn("atlas.recipes-impact", command_ids)
        self.assertIn("atlas.recipes-assess-plan", command_ids)
        self.assertTrue(str(result["catalog_digest"]).startswith("sha256:"))

    def test_doctor_reads_a_native_cleanroom_project_without_mutating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "example-mod"
            project.mkdir()
            (project / "settings.gradle").write_text(
                "rootProject.name = 'example-mod'\n", encoding="utf-8"
            )
            (project / "build.gradle").write_text(
                """plugins { id 'java' }
repositories { maven { url = 'https://maven.cleanroommc.com' } }
dependencies { implementation 'com.cleanroommc:cleanroom:0.6.8-alpha' }
""",
                encoding="utf-8",
            )
            (project / "gradle.properties").write_text(
                "minecraft_version=1.12.2\ncleanroom_version=0.6.8-alpha\n",
                encoding="utf-8",
            )
            before = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file()
            }

            result = _run_json("doctor", str(project), "--json")

            self.assertTrue(result["read_only"])
            self.assertEqual("cleanroom", result["target"]["platform"]["kind"])
            self.assertEqual("1.12.2", result["target"]["platform"]["minecraft_version"])
            after = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_public_inspect_recognizes_a_native_packwiz_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "supersymmetry"
            project.mkdir()
            for directory in ("config", "groovy", "mods"):
                (project / directory).mkdir()
            (project / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
            (project / "index.toml").write_text("", encoding="utf-8")
            for arguments in (
                ("init", "--quiet"),
                ("config", "user.name", "Workbench Smoke"),
                ("config", "user.email", "workbench@example.invalid"),
                ("add", "."),
                ("commit", "--quiet", "-m", "fixture"),
            ):
                subprocess.run(
                    ["git", "-C", str(project), *arguments],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            result = _run_json("inspect", str(project), "--json")

            context = result["workspace_context"]
            self.assertEqual("Supersymmetry", context["project"]["name"])
            self.assertTrue(context["project"]["index"]["matches_declared_hash"])
            self.assertEqual("1.12.2", context["platform"]["minecraft_version"])
            self.assertEqual("cleanroom-provisional", context["pack"]["selected_profile"])

    def test_public_feature_flow_applies_and_rolls_back_four_native_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _feature_checkout(root)
            state = root / "state"
            baseline = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file() and ".git" not in path.parts
            }

            plan = _run_json(
                "feature",
                "plan",
                "material-fluid-recipe",
                str(project),
                "--name",
                "Smoke Solvent",
                "--color",
                "425d73",
                "--recipe-script",
                "groovy/postInit/chemistry/Probe.groovy",
                "--recipe-map",
                "MIXER",
                "--input-fluid",
                "steam",
                "--state-root",
                str(state),
                "--json",
            )
            self.assertEqual(4, len(plan["operations"]))
            self.assertEqual("experimental-ready", plan["state"])

            applied = _run_json(
                "feature",
                "apply",
                "material-fluid-recipe",
                str(plan["id"]),
                "--consent",
                str(plan["id"]),
                "--state-root",
                str(state),
                "--json",
            )
            self.assertEqual("applied", applied["state"])

            rollback = _run_json(
                "feature",
                "rollback",
                "material-fluid-recipe",
                str(plan["id"]),
                str(applied["id"]),
                "--state-root",
                str(state),
                "--json",
            )
            self.assertEqual("restored", rollback["state"])
            restored = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file() and ".git" not in path.parts
            }
            self.assertEqual(baseline, restored)


if __name__ == "__main__":
    unittest.main()
