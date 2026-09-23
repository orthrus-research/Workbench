"""End-to-end tests for disposable Blueprint construction staging."""

from __future__ import annotations

import json
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
    SUITE_ROOT / "modules/atlas/src",
    SUITE_ROOT / "modules/blueprints/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.blueprint_stage import (  # noqa: E402
    BlueprintStageError,
    plan_material_backed_fluid,
    stage_material_backed_fluid,
    validate_retained_blueprint_stage,
)
from workbench_shell.runtime_plan import plan_project_runtime  # noqa: E402


PACK_REVISION = "9d3aa7ae0294bf27f0b8acbb893d61da23a06972"
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


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _project(parent: Path) -> Path:
    root = parent / "supersymmetry"
    root.mkdir()
    for directory in ("config", "mods"):
        (root / directory).mkdir()
        (root / directory / ".keep").write_text("", encoding="utf-8")
    (root / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
    (root / "index.toml").write_text("", encoding="utf-8")
    material_root = root / "groovy/material"
    material_root.mkdir(parents=True)
    builders = "\n\n".join(
        "        Existing{0} = new Material.Builder({0}, "
        "SuSyUtility.susyId('existing_{0}'))\n"
        "                .liquid()\n"
        "                .color(0x111111)\n"
        "                .build()".format(material_id)
        for material_id in (
            20000,
            20001,
            20002,
            20003,
            20004,
            20005,
            20006,
            20007,
            20009,
        )
    )
    (material_root / "PetrochemistryMaterials.groovy").write_text(
        "package material\n\n"
        "import static material.SuSyMaterials.*\n\n"
        "class PetrochemistryMaterials {\n\n"
        "    static void register() {\n\n"
        f"{builders}\n"
        "    }\n"
        "}",
        encoding="utf-8",
    )
    (material_root / "SuSyMaterials.groovy").write_text(
        "package material\n\n"
        "class SuSyMaterials {\n\n"
        "    // Petrochem Materials\n\n"
        "    public static Material Existing20000\n\n"
        "    // First Degree Materials A\n"
        "}\n",
        encoding="utf-8",
    )
    language = root / "resources/langfiles/lang/en_us.lang"
    language.parent.mkdir(parents=True)
    language.write_text(
        "# Fluids\n\n"
        "susy.material.existing_20000=Existing 20000\n"
        "\n# Thermodynamics\n",
        encoding="utf-8",
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Workbench Test")
    _git(root, "config", "user.email", "workbench@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root


def _baseline_queries(_workspace, queries, **_kwargs):
    results = {
        "CIT-PACK-DILUTED-OIL-MATERIAL": {
            "source": {
                "revision": PACK_REVISION,
                "path": "groovy/material/PetrochemistryMaterials.groovy",
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
        (
            f"SRC-PACK@{PACK_REVISION}:"
            "resources/langfiles/lang/en_us.lang"
            "#susy.material.diluted_oil_light"
        ): {
            "source": {
                "revision": PACK_REVISION,
                "path": "resources/langfiles/lang/en_us.lang",
                "file_sha256": (
                    "d23c3107bf54a6a25f59a0f129fb813"
                    "5e2e71dcc7b08f569fbc6c6db5009cb0a"
                ),
            },
            "localization": {
                "key": "susy.material.diluted_oil_light",
                "value": "Diluted Light Oil",
            },
        },
    }
    return [
        {
            "query_id": query["query_id"],
            "result_id": query["result_id"],
            "availability": "available",
            "result": results[query["query_id"]],
        }
        for query in queries
    ]


class BlueprintStageTest(unittest.TestCase):

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_plan_is_an_exact_read_only_three_file_diff(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            before = _git(project, "status", "--porcelain")

            first = plan_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
            )
            second = plan_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
            )

            self.assertEqual(first, second)
            self.assertEqual(first["state"], "ready")
            self.assertEqual(
                first["blueprint"]["effective_parameters"],
                {
                    "color": "0x425d73",
                    "material_id": 20008,
                    "name": "Pilot Coolant",
                    "registry_name": "pilot_coolant",
                    "symbol_name": "PilotCoolant",
                    "translation": "Pilot Coolant",
                },
            )
            self.assertEqual(
                [row["path"] for row in first["operations"]],
                [
                    "groovy/material/PetrochemistryMaterials.groovy",
                    "groovy/material/SuSyMaterials.groovy",
                    "resources/langfiles/lang/en_us.lang",
                ],
            )
            self.assertTrue(
                all(row["operation"] == "update" for row in first["operations"])
            )
            self.assertTrue(
                all(
                    row["diff"].startswith(f"--- a/{row['path']}\n")
                    for row in first["operations"]
                )
            )
            self.assertEqual(_git(project, "status", "--porcelain"), before)
            self.assertFalse((project / ".workbench").exists())

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_stages_real_candidate_in_clean_disposable_workspace(
        self,
        _resolve,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            state = root / "state"

            first = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )
            second = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )

            self.assertEqual(first["outcome"], "staged")
            self.assertEqual(second["outcome"], "reused")
            self.assertEqual(first["receipt"], second["receipt"])
            receipt = first["receipt"]
            reviewed = plan_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
            )
            target = receipt["target"]
            summary = {
                "state": receipt["state"],
                "outcome": first["outcome"],
                "stage_id": receipt["stage_id"],
                "candidate_id": receipt["blueprint"]["candidate_id"],
                "revision": target["revision"],
                "tracked_tree_id": target["tracked_tree_id"],
                "workspace_uri": target["workspace_uri"],
                "receipt_uri": target["receipt_uri"],
            }
            validated = validate_retained_blueprint_stage(summary, reviewed)
            self.assertEqual(validated["stage_id"], receipt["stage_id"])
            tampered_summary = dict(summary)
            tampered_summary["candidate_id"] = (
                "blueprints-convention-candidate:sha256:" + "f" * 64
            )
            with self.assertRaisesRegex(
                BlueprintStageError, "reviewed plan or summary"
            ):
                validate_retained_blueprint_stage(tampered_summary, reviewed)
            receipt_path = Path(target["receipt_uri"].removeprefix("file://"))
            original_receipt_bytes = receipt_path.read_bytes()
            rebound = json.loads(original_receipt_bytes)
            rebound["source"]["snapshot"]["tree_sha256"] = "sha256:" + "f" * 64
            candidate_material = {
                "plan_id": rebound["blueprint"]["plan_id"],
                "source_tree_sha256": rebound["source"]["snapshot"]["tree_sha256"],
            }
            rebound_candidate = "blueprints-convention-candidate:sha256:" + sha256(
                json.dumps(
                    candidate_material,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            rebound["blueprint"]["candidate_id"] = rebound_candidate
            stage_material = {
                "candidate_id": rebound_candidate,
                "census_id": rebound["atlas"]["census_id"],
                "reviewed_plan_id": reviewed["plan_id"],
                "source_revision": rebound["source"]["revision"],
                "source_tree_sha256": rebound["source"]["snapshot"]["tree_sha256"],
                "source_workspace_uri": rebound["source"]["workspace_uri"],
                "stage_revision": rebound["target"]["revision"],
                "stage_tree_id": rebound["target"]["tracked_tree_id"],
            }
            rebound_stage = "sha256:" + sha256(
                json.dumps(
                    stage_material,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            rebound["stage_id"] = rebound_stage
            receipt_path.write_text(
                json.dumps(rebound, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            rebound_summary = {
                **summary,
                "candidate_id": rebound_candidate,
                "stage_id": rebound_stage,
            }
            try:
                with self.assertRaisesRegex(
                    BlueprintStageError, "staged workspace differs"
                ):
                    validate_retained_blueprint_stage(rebound_summary, reviewed)
            finally:
                receipt_path.write_bytes(original_receipt_bytes)
            self.assertEqual(
                receipt["blueprint"]["effective_parameters"]["material_id"],
                20008,
            )
            stage = Path(
                receipt["target"]["workspace_uri"].removeprefix("file://")
            )
            declaration = stage / (
                "groovy/material/SuSyMaterials.groovy"
            )
            registration = stage / (
                "groovy/material/PetrochemistryMaterials.groovy"
            )
            self.assertIn(
                "public static Material PilotCoolant",
                declaration.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "PilotCoolant = new Material.Builder(20008, "
                "SuSyUtility.susyId('pilot_coolant'))",
                registration.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "susy.material.pilot_coolant=Pilot Coolant\n",
                (stage / "resources/langfiles/lang/en_us.lang").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertFalse((stage / "groovy/preInit").exists())
            self.assertFalse(
                (stage / "resources/susy_blueprint_pilot_coolant").exists()
            )
            self.assertEqual(
                [row["operation"] for row in receipt["outputs"]],
                ["update", "update", "update"],
            )
            self.assertEqual(_git(stage, "status", "--porcelain"), "")
            self.assertEqual(_git(project, "status", "--porcelain"), "")
            runtime_plan = plan_project_runtime(
                SUITE_ROOT,
                stage,
                side="client",
                launcher="prism",
            )
            self.assertEqual(runtime_plan["state"], "ready")
            self.assertEqual(runtime_plan["blockers"], [])

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_stage_honors_setup_selected_git_outside_path(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            selected_git = shutil.which("git")
            self.assertIsNotNone(selected_git)

            with patch.dict(
                os.environ,
                {
                    "PATH": "",
                    "WORKBENCH_GIT_EXECUTABLE": str(selected_git),
                },
            ):
                staged = stage_material_backed_fluid(
                    SUITE_ROOT,
                    project,
                    name="Selected Git Fluid",
                    color="0x425d73",
                    state_root=root / "state",
                )

            self.assertEqual("staged", staged["outcome"])
            self.assertTrue(
                Path(
                    staged["receipt"]["target"]["workspace_uri"].removeprefix(
                        "file://"
                    )
                ).is_dir()
            )

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_existing_registry_name_blocks_before_staging(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            material = (
                project / "groovy/material/PetrochemistryMaterials.groovy"
            )
            material.write_text(
                material.read_text(encoding="utf-8")
                + "new Material.Builder(20010, "
                "SuSyUtility.susyId('pilot_coolant'))\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                BlueprintStageError,
                "Blueprint plan is blocked",
            ):
                stage_material_backed_fluid(
                    SUITE_ROOT,
                    project,
                    name="Pilot Coolant",
                    color="0x425d73",
                    state_root=root / "state",
                )

            self.assertIn(
                "groovy/material/PetrochemistryMaterials.groovy",
                _git(project, "status", "--porcelain"),
            )
            stage_parent = root / "state/staging/blueprints"
            self.assertFalse(stage_parent.exists())

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_same_bytes_after_new_source_commit_get_fresh_provenance_stage(
        self,
        _resolve,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            state = root / "state"
            first = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )
            first_plan = first["receipt"]["blueprint"]["reviewed_plan_id"]
            first_source = first["receipt"]["source"]["revision"]

            _git(project, "commit", "--allow-empty", "-m", "Provenance only")
            second = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )
            third = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )

            self.assertEqual("staged", second["outcome"])
            self.assertEqual("reused", third["outcome"])
            self.assertEqual(second["receipt"], third["receipt"])
            self.assertEqual(
                first["receipt"]["blueprint"]["candidate_id"],
                second["receipt"]["blueprint"]["candidate_id"],
            )
            self.assertNotEqual(
                first["receipt"]["target"]["workspace_uri"],
                second["receipt"]["target"]["workspace_uri"],
            )
            self.assertNotEqual(
                first_plan,
                second["receipt"]["blueprint"]["reviewed_plan_id"],
            )
            self.assertNotEqual(
                first_source,
                second["receipt"]["source"]["revision"],
            )
            self.assertNotEqual(
                first["receipt"]["stage_id"],
                second["receipt"]["stage_id"],
            )

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_staging_rejects_state_and_nested_symlink_source_overlap(
        self,
        _resolve,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            with self.assertRaisesRegex(
                BlueprintStageError,
                "state root cannot overlap",
            ):
                stage_material_backed_fluid(
                    SUITE_ROOT,
                    project,
                    name="Pilot Coolant",
                    color="0x425d73",
                    state_root=project / ".state",
                )
            self.assertFalse((project / ".state").exists())

            state = root / "state"
            state.mkdir()
            (state / "staging").symlink_to(project, target_is_directory=True)
            with self.assertRaisesRegex(
                BlueprintStageError,
                "cannot traverse a symbolic link",
            ):
                stage_material_backed_fluid(
                    SUITE_ROOT,
                    project,
                    name="Pilot Coolant",
                    color="0x425d73",
                    state_root=state,
                )
            self.assertFalse((project / "blueprints").exists())

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_reused_stage_rejects_tampered_target_uris(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            state = root / "state"
            first = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )
            receipt_path = Path(
                first["receipt"]["target"]["receipt_uri"].removeprefix("file://")
            )
            retained = json.loads(receipt_path.read_text(encoding="utf-8"))
            retained["target"]["workspace_uri"] = project.as_uri()
            receipt_path.write_text(
                json.dumps(retained, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                BlueprintStageError,
                "different candidate",
            ):
                stage_material_backed_fluid(
                    SUITE_ROOT,
                    project,
                    name="Pilot Coolant",
                    color="0x425d73",
                    state_root=state,
                )

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_reused_stage_rejects_committed_unreviewed_tree_content(
        self,
        _resolve,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            state = root / "state"
            first = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )
            workspace = Path(
                first["receipt"]["target"]["workspace_uri"].removeprefix(
                    "file://"
                )
            )
            extra = workspace / "groovy/postInit/Unreviewed.groovy"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("throw new RuntimeException('unreviewed')\n", encoding="utf-8")
            _git(workspace, "add", ".")
            _git(
                workspace,
                "-c",
                "user.name=Workbench Test",
                "-c",
                "user.email=workbench@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "unreviewed executable",
            )
            receipt_path = Path(
                first["receipt"]["target"]["receipt_uri"].removeprefix(
                    "file://"
                )
            )
            retained = json.loads(receipt_path.read_text(encoding="utf-8"))
            retained["target"]["revision"] = _git(workspace, "rev-parse", "HEAD")
            retained["target"]["tracked_tree_id"] = _git(
                workspace, "rev-parse", "HEAD^{tree}"
            )
            receipt_path.write_text(
                json.dumps(retained, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                BlueprintStageError,
                "different candidate",
            ):
                stage_material_backed_fluid(
                    SUITE_ROOT,
                    project,
                    name="Pilot Coolant",
                    color="0x425d73",
                    state_root=state,
                )


if __name__ == "__main__":
    unittest.main()
