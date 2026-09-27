"""End-to-end tests for disposable Blueprint construction staging."""

from __future__ import annotations

from contextlib import redirect_stdout
import base64
import io
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
    _load_json,
    _prepare_stage_parent,
    _run_git,
    _write_json,
    plan_material_backed_fluid,
    stage_material_backed_fluid,
    validate_retained_blueprint_stage,
)
from workbench_shell.runtime_plan import plan_project_runtime  # noqa: E402
from workbench_shell.cli import main as shell_main  # noqa: E402
from workbench_api import ExecutionContext  # noqa: E402
from workbench_shell import commands  # noqa: E402
from workbench_core.host_services import install_local_host_services  # noqa: E402
import workbench_api.processes as process_port  # noqa: E402
from workbench_api.source_transactions import source_transactions_scope  # noqa: E402
import workbench_shell.blueprint_stage as stage_module  # noqa: E402


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
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()

    def test_real_git_runs_through_core_process_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _git(root, "init", "--quiet")
            with patch.object(
                stage_module, "execute_process", wraps=process_port.execute_process
            ) as core_process:
                observed = _run_git(root, ("rev-parse", "--is-inside-work-tree"))
            self.assertEqual(b"true\n", observed)
            command = core_process.call_args.args[0]
            self.assertTrue(Path(command[0]).is_absolute())
            self.assertEqual(("-C", str(root), "rev-parse", "--is-inside-work-tree"), tuple(command[1:]))
            self.assertEqual(root, core_process.call_args.kwargs["cwd"])
            self.assertEqual(b"", core_process.call_args.kwargs["stdin"])
            self.assertEqual(60, core_process.call_args.kwargs["timeout_seconds"])
            self.assertEqual(4 * 1024 * 1024, core_process.call_args.kwargs["output_limit"])

    def test_git_refuses_an_unbound_core_process_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(stage_module, "_git_executable", return_value=sys.executable),
                patch.object(process_port, "_host", None),
            ):
                with self.assertRaisesRegex(BlueprintStageError, "no process host is bound"):
                    _run_git(Path(temporary), ("status",))

    @unittest.skipUnless(os.name == "posix", "executable test fixture requires POSIX")
    def test_git_nonzero_and_timeout_refuse_without_stage_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "fixture-git"
            script.write_text("#!/bin/sh\nprintf 'fixture Git failure' >&2\nexit 17\n", encoding="utf-8")
            script.chmod(0o700)
            with patch.object(stage_module, "_git_executable", return_value=str(script)):
                with self.assertRaisesRegex(BlueprintStageError, "fixture Git failure"):
                    _run_git(root, ("status",))

            script.write_text("#!/bin/sh\nsleep 3\n", encoding="utf-8")

            def short_timeout(*args, **kwargs):
                self.assertEqual(60, kwargs["timeout_seconds"])
                return process_port.execute_process(
                    *args, **{**kwargs, "timeout_seconds": 0.2}
                )

            with (
                patch.object(stage_module, "_git_executable", return_value=str(script)),
                patch.object(stage_module, "execute_process", side_effect=short_timeout),
            ):
                with self.assertRaisesRegex(BlueprintStageError, "timed out"):
                    _run_git(root, ("status",))

    @unittest.skipUnless(os.name == "posix", "background child fixture requires POSIX")
    def test_git_refuses_a_background_child_after_its_leader_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "fixture-git"
            script.write_text("#!/bin/sh\nsleep 3 &\nexit 0\n", encoding="utf-8")
            script.chmod(0o700)
            with patch.object(stage_module, "_git_executable", return_value=str(script)):
                with self.assertRaisesRegex(BlueprintStageError, "native-tool invocation cancelled"):
                    _run_git(root, ("status",))

    def test_stage_receipt_uses_core_create_once_with_historical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            value = {"format": "example", "name": "é", "state": "staged"}
            _write_json(path, value)
            self.assertEqual(
                (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                path.read_bytes(),
            )
            legacy = Path(directory) / "historical-receipt.json"
            legacy.write_bytes(path.read_bytes())
            self.assertEqual(value, _load_json(legacy))
            with self.assertRaisesRegex(BlueprintStageError, "already exists"):
                _write_json(path, value)

    def test_sealed_update_uses_core_source_custody_and_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            target = workspace / "example.groovy"
            before, after = b"old source\n", b"new source\n"
            target.write_bytes(before)
            target.chmod(0o755)
            sealed = {"operations": [{
                "operation": "update", "path": target.name,
                "before_sha256": sha256(before).hexdigest(),
                "content_sha256": sha256(after).hexdigest(),
                "content_base64": base64.b64encode(after).decode("ascii"),
            }]}

            with source_transactions_scope(None):
                with self.assertRaisesRegex(
                    BlueprintStageError, "protected source edits require Workbench Core",
                ):
                    stage_module._apply_sealed_operations(workspace, sealed)
            self.assertEqual(before, target.read_bytes())

            outputs = stage_module._apply_sealed_operations(workspace, sealed)
            self.assertEqual(after, target.read_bytes())
            self.assertEqual(0o755, target.stat().st_mode & 0o777)
            self.assertEqual(sha256(after).hexdigest(), outputs[0]["sha256"])

    def test_user_session_root_is_direct_and_passed_through_core_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            sessions = base / "saved-blueprint-sessions"
            self.assertEqual(
                sessions,
                _prepare_stage_parent(sessions, workspace, direct=True),
            )
            self.assertFalse((sessions / "staging/blueprints").exists())
            context = ExecutionContext(workspace, base / "state", locations={"blueprint_sessions": sessions})
            with patch("workbench_shell.cli.main", return_value=0) as main:
                self.assertEqual(0, commands.blueprint_stage(["--help"], context=context))
            self.assertEqual(sessions, main.call_args.kwargs["resolved_locations"]["blueprint_sessions"])
            with patch("workbench_shell.cli.stage_material_backed_fluid", return_value={"format": "test"}) as stage:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(0, shell_main([
                        "blueprint-stage", str(workspace), "--suite-root", str(SUITE_ROOT),
                        "--name", "Test", "--color", "0x123456", "--json",
                    ], resolved_locations=context.locations))
            self.assertEqual(sessions, stage.call_args.kwargs["session_root"])

    @patch(
        "workbench_atlas.material_census.resolve_standard_queries",
        side_effect=_baseline_queries,
    )
    def test_core_session_location_retains_and_reuses_real_stage(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            sessions = root / "selected-blueprint-sessions"
            context = ExecutionContext(
                project,
                root / "operation-state",
                locations={"blueprint_sessions": sessions},
            )
            arguments = [
                str(project),
                "--suite-root", str(SUITE_ROOT),
                "--name", "Pilot Coolant",
                "--color", "0x425d73",
                "--json",
            ]
            results = []
            for _ in range(2):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(
                        0, commands.blueprint_stage(arguments, context=context)
                    )
                results.append(json.loads(output.getvalue()))
            self.assertEqual(["staged", "reused"], [row["outcome"] for row in results])
            self.assertEqual(results[0]["receipt"], results[1]["receipt"])
            receipt = results[0]["receipt"]
            receipt_path = Path(receipt["target"]["receipt_uri"].removeprefix("file://"))
            self.assertEqual(sessions, receipt_path.parent.parent)
            self.assertEqual(receipt, json.loads(receipt_path.read_text(encoding="utf-8")))
            self.assertFalse((root / "operation-state/staging/blueprints").exists())
            self.assertEqual("", _git(project, "status", "--porcelain"))


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
            # Pre-Core V2 receipts used the same ordinary file and exact bytes.
            receipt_path.unlink()
            receipt_path.write_bytes(original_receipt_bytes)
            historical_reuse = stage_material_backed_fluid(
                SUITE_ROOT,
                project,
                name="Pilot Coolant",
                color="0x425d73",
                state_root=state,
            )
            self.assertEqual("reused", historical_reuse["outcome"])
            self.assertEqual(receipt, historical_reuse["receipt"])
            self.assertEqual(original_receipt_bytes, receipt_path.read_bytes())
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
            alias = receipt_path.with_name("receipt-hardlink.json")
            os.link(receipt_path, alias)
            try:
                with self.assertRaisesRegex(BlueprintStageError, "receipt is invalid"):
                    validate_retained_blueprint_stage(summary, reviewed)
                with self.assertRaisesRegex(BlueprintStageError, "receipt is invalid"):
                    stage_material_backed_fluid(
                        SUITE_ROOT, project, name="Pilot Coolant", color="0x425d73",
                        state_root=state,
                    )
            finally:
                alias.unlink()
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
