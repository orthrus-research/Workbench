"""End-to-end tests for read-only retained-runtime diagnosis."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from test_classfile import mixin_class, target_class  # noqa: E402
from packwiz_v2_fixture import seal_packwiz_v2_receipt  # noqa: E402
from workbench_project_intelligence import ProjectInspectionError  # noqa: E402
from workbench_shell.runtime_diagnose import (  # noqa: E402
    RuntimeDiagnosisError,
    diagnose_project_runtime,
    load_verified_runtime_evidence,
)
from workbench_shell.runtime_recipe_reload_diagnostic import (  # noqa: E402
    RuntimeRecipeDiagnosticError,
    compare_project_recipe_reload,
    diagnose_project_recipe_reload,
)
from workbench_shell.runtime_recipe_invalidation_diagnostic import (  # noqa: E402
    RuntimeRecipeInvalidationError,
    compare_project_recipe_invalidations,
    diagnose_project_recipe_invalidations,
)
import workbench_shell.runtime_recipe_invalidation_diagnostic as invalidation_runtime  # noqa: E402


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

CRASH = "\n".join((
    "---- Minecraft Crash Report ----",
    "Description: Initializing game",
    "",
    (
        "net.minecraftforge.fml.common.LoaderExceptionModCrash: "
        "Caught exception from Example"
    ),
    "Caused by: java.lang.NoClassDefFoundError: example/Target",
    "Caused by: java.lang.ClassNotFoundException: example.Target",
    (
        "Caused by: org.spongepowered.asm.mixin.transformer.throwables."
        "MixinTransformerError: An unexpected critical error was encountered"
    ),
    (
        "Caused by: org.spongepowered.asm.mixin.injection.throwables."
        "InjectionError: Critical injection failure: Variable modifier method "
        "setBlock(I)I in mixins.example.json:Mixin from mod example failed "
        "injection check, (0/1) succeeded. Scanned 0 target(s). Using refmap "
        "mixins.example.refmap.json"
    ),
    "",
    "Mixins in Stacktrace:",
    "    example/Target:",
    "        example.Mixin (mixins.example.json) [example]",
    "",
    "-- System Details --",
    "  FML: MCP 9.42 Cleanroom 0.6.8-alpha 2 mods loaded, 2 mods active",
    "",
))

LATEST_LOG = "\n".join((
    "java.lang.IllegalArgumentException: unrelated configuration warning",
    "com.google.gson.JsonSyntaxException: unrelated recipe warning",
    "Caused by: com.google.gson.JsonSyntaxException: another recipe warning",
    "Forge Mod Loader has successfully loaded 210 mods",
    "",
))


def _run(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _project(parent: Path) -> Path:
    project = parent / "pack"
    project.mkdir()
    for directory in ("config", "groovy", "mods"):
        (project / directory).mkdir()
    (project / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
    (project / "index.toml").write_text("", encoding="utf-8")
    _run(project, "init", "--quiet")
    _run(project, "config", "user.name", "Workbench Test")
    _run(project, "config", "user.email", "workbench@example.invalid")
    _run(project, "add", ".")
    _run(project, "commit", "--quiet", "-m", "fixture")
    return project


def _tree_identity(root: Path) -> dict[str, object]:
    entries = []
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        payload = path.read_bytes()
        entries.append({
            "mode": stat.S_IMODE(path.stat().st_mode),
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        })
    canonical = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "tree_sha256": "sha256:" + sha256(canonical).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
    }


def _launch_fixture(root: Path) -> tuple[Path, Path, list[Path]]:
    instance = root / "instance"
    minecraft = instance / ".minecraft"
    mods = minecraft / "mods"
    mods.mkdir(parents=True)
    target_jar = mods / "target.jar"
    mixin_jar = mods / "mixin.jar"
    with zipfile.ZipFile(target_jar, "w") as archive:
        archive.writestr("example/Target.class", target_class())
    with zipfile.ZipFile(mixin_jar, "w") as archive:
        archive.writestr("example/Mixin.class", mixin_class())
        archive.writestr("mixins.example.json", "{}\n")

    materialization_id = "sha256:" + ("3" * 64)
    payload = _tree_identity(minecraft)
    receipt_root = root / "receipts"
    receipt_root.mkdir()
    materialization_path = receipt_root / "packwiz-materialization-v2.json"
    materialization_receipt = {
        "format": "workbench-packwiz-materialization-receipt-v2",
        "schema_version": 2,
        "state": "materialized",
        "materialization_id": materialization_id,
        "payload": {
            **payload,
            "root_uri": minecraft.as_uri(),
        },
        "target": {
            "instance_root_uri": instance.as_uri(),
            "receipt_uri": materialization_path.as_uri(),
        },
    }
    materialization_receipt = seal_packwiz_v2_receipt(
        materialization_receipt
    )
    materialization_id = materialization_receipt["materialization_id"]
    materialization_path.write_text(
        json.dumps(materialization_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    evidence_root = root / "evidence"
    evidence_root.mkdir()
    crash = evidence_root / "crash.txt"
    crash.write_text(CRASH, encoding="utf-8")
    crash_bytes = crash.read_bytes()
    receipt = {
        "format": "workbench-runtime-launch-receipt-v1",
        "schema_version": 1,
        "launch_id": "sha256:" + ("1" * 64),
        "materialization_id": materialization_id,
        "outcome": "failed",
        "evidence": [
            {
                "label": "minecraft-crash-report",
                "state": "captured",
                "capture_uri": crash.as_uri(),
                "sha256": sha256(crash_bytes).hexdigest(),
                "size": len(crash_bytes),
            }
        ],
        "launcher": {
            "projection_uri": (root / "deleted-projection").as_uri()
        },
        "projection": {
            "projection_uri": (root / "deleted-projection").as_uri(),
            "source_instance_uri": instance.as_uri(),
            "payload": {
                "materialized": payload,
            },
        },
    }
    receipt_path = evidence_root / "runtime-launch-v1.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt_path, crash, [target_jar, mixin_jar]


def _checkpoint_fixture(root: Path, latest_text: str = LATEST_LOG) -> Path:
    evidence_root = root / "checkpoint-evidence"
    evidence_root.mkdir()
    latest_log = evidence_root / "latest.log"
    latest_log.write_text(latest_text, encoding="utf-8")
    payload = latest_log.read_bytes()
    receipt = {
        "format": "workbench-runtime-launch-receipt-v2",
        "schema_version": 2,
        "launch_id": "sha256:" + ("4" * 64),
        "outcome": "checkpoint-reached",
        "project": {
            "name": "Supersymmetry",
            "version": "test",
            "minecraft_version": "1.12.2",
        },
        "evidence": [{
            "label": "minecraft-latest-log",
            "state": "captured",
            "capture_uri": latest_log.as_uri(),
            "sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        }],
        "observation": {
            "checkpoint": {
                "id": "fml-client-loaded",
                "marker": "Forge Mod Loader has successfully loaded 210 mods",
                "source": "minecraft-latest-log",
            },
        },
        "limitations": [
            "The loader checkpoint is not a visual main-menu assertion."
        ],
    }
    receipt_path = evidence_root / "runtime-launch-v2.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt_path


def _recipe_checkpoint_fixture(root: Path) -> tuple[Path, Path]:
    groovy_log = root / "groovy.log"
    groovy_log.write_text(
        "\n".join((
            "[07:30:44] [SERVER/INFO] [supersymmetry]: Running scripts in loader 'postInit'",
            "[07:31:30] [SERVER/INFO] [supersymmetry]: Running scripts in loader 'postInit'",
            (
                "[07:31:31] [SERVER/WARN] [postInit.materials.metallurgy.Quenching]: "
                "Recipe duplicate or conflict found in RecipeMap quencher and was not added. "
                "See next lines for details"
            ),
            (
                "[07:31:31] [SERVER/WARN] [postInit.materials.metallurgy.Quenching]: "
                "Attempted to add Recipe: gregtech.api.recipes.Recipe@1234"
            ),
            (
                "[07:31:31] [SERVER/WARN] [postInit.materials.metallurgy.Quenching]: "
                "Which conflicts with: gregtech.api.recipes.Recipe@5678"
            ),
            "",
        )),
        encoding="utf-8",
    )
    payload = groovy_log.read_bytes()
    evidence = {
        "label": "minecraft-groovy-log",
        "state": "captured",
        "capture_uri": groovy_log.as_uri(),
        "sha256": sha256(payload).hexdigest(),
        "size": len(payload),
    }
    receipt_path = _v3_checkpoint_fixture(
        root,
        extra_evidence=(evidence,),
    )
    return receipt_path, groovy_log


def _cold_recipe_checkpoint_fixture(
    root: Path,
    lane: str,
    groups: list[tuple[str, str, int]],
) -> tuple[Path, Path]:
    lane_root = root / lane
    lane_root.mkdir()
    groovy_log = lane_root / "groovy.log"
    lines = [
        "[07:30:44] [SERVER/INFO] [supersymmetry]: "
        "Running scripts in loader 'postInit'"
    ]
    ordinal = 0
    for logger, recipe_map, count in groups:
        for _ in range(count):
            lines.extend((
                (
                    f"[07:31:31] [SERVER/WARN] [{logger}]: Recipe duplicate "
                    f"or conflict found in RecipeMap {recipe_map} and was not "
                    "added. See next lines for details"
                ),
                (
                    f"[07:31:31] [SERVER/WARN] [{logger}]: Attempted to add "
                    f"Recipe: gregtech.api.recipes.Recipe@{lane}{ordinal}"
                ),
                (
                    f"[07:31:31] [SERVER/WARN] [{logger}]: Which conflicts "
                    f"with: gregtech.api.recipes.Recipe@other{lane}{ordinal}"
                ),
            ))
            ordinal += 1
    lines.append(
        "[07:31:32] [SERVER/INFO] [supersymmetry]: Groovy scripts took "
        "1ms to compile and 2ms to run in postInit."
    )
    groovy_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = groovy_log.read_bytes()
    evidence = {
        "label": "minecraft-groovy-log",
        "state": "captured",
        "capture_uri": groovy_log.as_uri(),
        "sha256": sha256(payload).hexdigest(),
        "size": len(payload),
    }
    return (
        _v3_checkpoint_fixture(lane_root, extra_evidence=(evidence,)),
        groovy_log,
    )


def _v3_checkpoint_fixture(
    root: Path,
    *,
    state: str = "exited",
    boundary: str = "projected-client-process-exit",
    extra_evidence: tuple[dict[str, object], ...] = (),
    latest_text: str = LATEST_LOG,
) -> Path:
    parent_receipt = _checkpoint_fixture(root, latest_text)
    if extra_evidence:
        parent = json.loads(parent_receipt.read_text(encoding="utf-8"))
        parent["evidence"].extend(extra_evidence)
        parent_receipt.write_text(
            json.dumps(parent, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    parent_payload = parent_receipt.read_bytes()
    launch = json.loads(parent_payload.decode("utf-8"))
    parent_launch_id = launch["launch_id"]
    launch["format"] = "workbench-runtime-launch-receipt-v3"
    launch["schema_version"] = 3
    launch["parent_launch_receipt"] = {
        "launch_id": parent_launch_id,
        "uri": parent_receipt.as_uri(),
        "sha256": sha256(parent_payload).hexdigest(),
        "size": len(parent_payload),
    }
    launch["observation"]["session_exit"] = {
        "state": state,
        "method": "windows-cim-instance-id",
        "samples": 4,
        "observed_pids": [42],
        "attached_at": "2026-08-02T18:40:00.000Z",
        "observed_at": "2026-08-02T18:45:00.000Z",
    }
    launch["launch_policy"] = {"observation_boundary": boundary}
    launch["target"] = {"parent_receipt_uri": parent_receipt.as_uri()}
    identity = {
        "parent_launch_id": parent_launch_id,
        "session_exit": launch["observation"]["session_exit"],
        "evidence": [
            {
                key: item.get(key)
                for key in ("label", "state", "sha256", "size")
            }
            for item in launch["evidence"]
        ],
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    launch["launch_id"] = "sha256:" + sha256(canonical).hexdigest()
    receipt = parent_receipt.parent / "runtime-launch-v3.json"
    receipt.write_text(
        json.dumps(launch, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _feature_recipe_checkpoint_fixture(
    root: Path,
    lane: str,
    *,
    groovy_conflicts: int,
    java_duplicates: int,
    java_runtime_id: str = "sha256:" + ("7" * 64),
) -> Path:
    lane_root = root / lane
    lane_root.mkdir()
    marker = "Forge Mod Loader has successfully loaded 210 mods"
    latest_lines = [
        "[07:30:00] [Client thread/INFO] [GregTech]: Registering recipes..."
    ]
    for ordinal in range(java_duplicates):
        latest_lines.extend((
            "[07:30:01] [Client thread/WARN] [GregTech]: Invalid Recipe Found",
            (
                "java.lang.IllegalArgumentException: Tried to register duplicate "
                f"Furnace Recipe: 1x example:Ore {ordinal} -> 1x gregtech:Ingot, 0.5exp"
            ),
            "\tat gregtech.api.recipes.ModHandler.logInvalidRecipe(ModHandler.java:755)",
            "\tat gregtech.api.recipes.ModHandler.addSmeltingRecipe(ModHandler.java:159)",
            "\tat gregtech.api.recipes.ModHandler.addSmeltingRecipe(ModHandler.java:135)",
            "\tat example.RecipeLoader.register(RecipeLoader.java:42)",
        ))
    latest_lines.append(f"[07:31:00] [Client thread/INFO] [FML]: {marker}")

    groovy_log = lane_root / "groovy.log"
    groovy_lines = [
        "[07:30:44] [SERVER/INFO] [supersymmetry]: "
        "Running scripts in loader 'postInit'"
    ]
    for ordinal in range(groovy_conflicts):
        groovy_lines.extend((
            (
                "[07:30:45] [SERVER/WARN] [postInit.Example]: Recipe duplicate "
                "or conflict found in RecipeMap mixer and was not added. "
                "See next lines for details"
            ),
            (
                "[07:30:45] [SERVER/WARN] [postInit.Example]: Attempted to add "
                f"Recipe: Recipe@{lane}{ordinal}"
            ),
            (
                "[07:30:45] [SERVER/WARN] [postInit.Example]: Which conflicts "
                f"with: Recipe@other{ordinal}"
            ),
        ))
    groovy_lines.append(
        "[07:30:46] [SERVER/INFO] [supersymmetry]: Groovy scripts took "
        "1ms to compile and 2ms to run in postInit."
    )
    groovy_log.write_text("\n".join(groovy_lines) + "\n", encoding="utf-8")
    groovy_payload = groovy_log.read_bytes()
    receipt = _v3_checkpoint_fixture(
        lane_root,
        latest_text="\n".join(latest_lines) + "\n",
        extra_evidence=({
            "label": "minecraft-groovy-log",
            "state": "captured",
            "capture_uri": groovy_log.as_uri(),
            "sha256": sha256(groovy_payload).hexdigest(),
            "size": len(groovy_payload),
        },),
    )
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["java"] = {"runtime_id": java_runtime_id}
    value["launcher"] = {
        "family": "test-launcher",
        "host": {"os": "test", "architecture": "x64"},
    }
    receipt.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


class RuntimeDiagnosisTest(unittest.TestCase):
    def test_accepts_close_observed_v3_launch_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _v3_checkpoint_fixture(root)

            result = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            self.assertEqual(result["source"]["launch_outcome"], "checkpoint-reached")
            self.assertEqual(result["checkpoint"]["id"], "fml-client-loaded")

    def test_rejects_relabelled_v1_as_v3_launch_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _checkpoint_fixture(root)
            launch = json.loads(receipt.read_text(encoding="utf-8"))
            launch["format"] = "workbench-runtime-launch-receipt-v3"
            launch["schema_version"] = 3
            receipt.write_text(
                json.dumps(launch, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeDiagnosisError,
                "parent or observation identity",
            ):
                diagnose_project_runtime(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )

    def test_rejects_timeout_mislabeled_as_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _v3_checkpoint_fixture(
                root,
                state="session-timeout",
                boundary="projected-client-process-exit",
            )

            with self.assertRaisesRegex(
                RuntimeDiagnosisError,
                "mislabeled as process-exit evidence",
            ):
                diagnose_project_runtime(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )

    def test_rejects_tampered_v3_lifecycle_with_unchanged_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _v3_checkpoint_fixture(root)
            launch = json.loads(receipt.read_text(encoding="utf-8"))
            launch["observation"]["session_exit"]["observed_pids"] = [43]
            receipt.write_text(
                json.dumps(launch, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeDiagnosisError,
                "does not bind its lifecycle and evidence",
            ):
                diagnose_project_runtime(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )

    def test_confirms_named_variable_mismatch_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, _crash, jars = _launch_fixture(root)
            before = {jar: jar.read_bytes() for jar in jars}

            first = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )
            second = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            self.assertEqual(first, second)
            self.assertEqual(
                first["format"],
                "workbench-runtime-diagnosis-v2",
            )
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["state"], "blocked")
            self.assertEqual(first["operation_class"], "read-only")
            self.assertEqual(first["authority"], {
                "classification": "integration-observation",
                "normative": False,
                "atlas_publication": False,
                "sentinel_policy_finding": False,
            })
            self.assertEqual(
                first["findings"][0]["category"],
                "mixin-local-variable-discriminator",
            )
            self.assertEqual(
                first["findings"][0]["facts"]["requested_names"],
                ["arg3"],
            )
            self.assertEqual(
                first["findings"][0]["facts"][
                    "available_argument_names"
                ],
                ["flag", "pos", "state"],
            )
            self.assertEqual(len(first["wrappers"]), 2)
            self.assertEqual(
                first["artifact_roots"][-1]["source"],
                "materialized-source",
            )
            self.assertEqual(
                first["artifact_roots"][-1]["state"],
                "available",
            )
            self.assertEqual(
                first["artifact_roots"][-1]["provenance"]["state"],
                "verified",
            )
            self.assertEqual(
                first["findings"][0]["artifact_binding"]["state"],
                "verified",
            )
            self.assertEqual(
                first["findings"][0]["confidence"],
                "confirmed",
            )
            self.assertEqual(
                {item["role"] for item in first["artifact_observations"]},
                {"target-class", "mixin-class", "mixin-config"},
            )
            self.assertEqual(before, {jar: jar.read_bytes() for jar in jars})

            schema = json.loads(
                (
                    MODULE_ROOT
                    / "schemas/runtime-diagnosis-v2.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(first)

    def test_materialized_source_provenance_stays_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, _crash, _jars = _launch_fixture(root)

            result = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            provenance = result["artifact_roots"][-1]["provenance"]
            self.assertEqual(provenance["state"], "verified")
            self.assertEqual(
                provenance["receipt_uri"],
                (
                    root
                    / "receipts/packwiz-materialization-v2.json"
                ).resolve().as_uri(),
            )

    def test_downgrades_artifact_finding_when_payload_drifted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, _crash, jars = _launch_fixture(root)
            jars[0].write_bytes(jars[0].read_bytes() + b"drift")

            result = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            self.assertEqual(result["state"], "blocked")
            self.assertEqual(
                result["artifact_roots"][-1]["provenance"]["state"],
                "drifted",
            )
            self.assertEqual(result["findings"][0]["confidence"], "observed")
            self.assertEqual(
                result["findings"][0]["artifact_binding"]["state"],
                "drifted",
            )
            self.assertNotIn("guidance", result["findings"][0])
            self.assertTrue(any(
                "not bound to the launched payload" in limitation
                for limitation in result["limitations"]
            ))

    def test_downgrades_source_superseded_by_projection_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, _crash, _jars = _launch_fixture(root)
            launch = json.loads(receipt.read_text(encoding="utf-8"))
            launch["format"] = "workbench-runtime-launch-receipt-v2"
            launch["schema_version"] = 2
            launch["projection"]["compatibility_patches"] = [{
                "patch_id": "workbench-pack:test:patch-v1",
            }]
            receipt.write_text(
                json.dumps(launch, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            result = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            provenance = result["artifact_roots"][-1]["provenance"]
            self.assertEqual(provenance["state"], "unverified")
            self.assertIn("compatibility patches", provenance["reason"])
            self.assertEqual(result["findings"][0]["confidence"], "observed")
            self.assertEqual(
                result["findings"][0]["artifact_binding"]["state"],
                "unverified",
            )
            self.assertNotIn("guidance", result["findings"][0])

    def test_checkpoint_has_no_causal_exception_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _checkpoint_fixture(root)

            result = diagnose_project_runtime(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            self.assertEqual(result["state"], "checkpoint-reached")
            self.assertEqual(result["checkpoint"], {
                "id": "fml-client-loaded",
                "marker": "Forge Mod Loader has successfully loaded 210 mods",
                "source": "minecraft-latest-log",
            })
            self.assertIsNone(result["analysis_evidence_label"])
            self.assertEqual(result["exception_chain"], [])
            self.assertIsNone(result["primary_failure"])
            self.assertEqual(result["artifact_roots"], [])
            self.assertEqual(result["artifact_observations"], [])
            self.assertEqual(
                {
                    item["type"]: item["count"]
                    for item in result["log_observations"]
                },
                {
                    "com.google.gson.JsonSyntaxException": 2,
                    "java.lang.IllegalArgumentException": 1,
                },
            )
            self.assertEqual(
                result["limitations"],
                ["The loader checkpoint is not a visual main-menu assertion."],
            )
            schema = json.loads(
                (
                    MODULE_ROOT
                    / "schemas/runtime-diagnosis-v2.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(result)

    def test_recipe_reload_diagnostic_uses_verified_groovy_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, groovy_log = _recipe_checkpoint_fixture(root)

            first = diagnose_project_recipe_reload(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )
            second = diagnose_project_recipe_reload(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            self.assertEqual(first, second)
            self.assertEqual(
                first["format"],
                "workbench-supersymmetry-recipe-reload-diagnostic-v1",
            )
            self.assertEqual(first["operation_class"], "read-only")
            self.assertEqual(first["state"], "attention")
            self.assertEqual(first["summary"]["complete_conflict_count"], 1)
            self.assertEqual(first["summary"]["reload_conflict_count"], 1)
            self.assertEqual(
                first["groups"][0]["script_logger"],
                "postInit.materials.metallurgy.Quenching",
            )
            self.assertEqual(first["groups"][0]["recipe_map"], "quencher")
            self.assertEqual(
                first["recommendation"]["state"],
                "restart-required",
            )
            self.assertEqual(
                first["source"]["evidence"]["capture_uri"],
                groovy_log.resolve().as_uri(),
            )
            self.assertEqual(first["profile"]["maturity"], "experimental")
            self.assertEqual(
                first["source"]["pack_profile"]["launch_binding"]["state"],
                "unverified-current-diagnostic-context",
            )
            self.assertTrue(
                first["profile"]["contract_uri"].endswith(
                    "/profiles/packs/supersymmetry/atlas/recipe-reload-diagnostic-v1.md"
                )
            )

    def test_small_text_evidence_is_hashed_and_decoded_from_one_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = _checkpoint_fixture(Path(temporary))

            with mock.patch(
                "workbench_shell.runtime_diagnose.sha256_file",
                side_effect=AssertionError("small text evidence was re-read"),
            ):
                _receipt, _identity, evidence, texts, _limitations = (
                    load_verified_runtime_evidence(receipt)
                )

            self.assertEqual(evidence[0]["state"], "verified")
            self.assertEqual(texts, [("minecraft-latest-log", LATEST_LOG)])

    def test_compares_two_verified_cold_start_groovy_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            baseline_receipt, baseline_log = _cold_recipe_checkpoint_fixture(
                root,
                "baseline",
                [
                    ("postInit.chemistry.Catalysts", "blender", 1),
                    ("postInit.old.Script", "mixer", 1),
                ],
            )
            candidate_receipt, candidate_log = _cold_recipe_checkpoint_fixture(
                root,
                "candidate",
                [
                    ("postInit.chemistry.Catalysts", "blender", 3),
                    ("postInit.new.Script", "boiler", 1),
                ],
            )

            first = compare_project_recipe_reload(
                REPOSITORY_ROOT,
                project,
                baseline_launch_receipt=baseline_receipt,
                candidate_launch_receipt=candidate_receipt,
            )
            second = compare_project_recipe_reload(
                REPOSITORY_ROOT,
                project,
                baseline_launch_receipt=baseline_receipt,
                candidate_launch_receipt=candidate_receipt,
            )

            self.assertEqual(first, second)
            self.assertEqual(
                first["format"],
                "workbench-supersymmetry-groovy-conflict-group-comparison-v1",
            )
            self.assertEqual(first["state"], "more-observed")
            self.assertEqual(
                first["summary"]["baseline_complete_conflict_count"], 2
            )
            self.assertEqual(
                first["summary"]["candidate_complete_conflict_count"], 4
            )
            self.assertEqual(first["summary"]["newly_observed_group_count"], 1)
            self.assertEqual(first["summary"]["increased_group_count"], 1)
            self.assertEqual(
                first["summary"]["no_longer_observed_group_count"], 1
            )
            self.assertEqual(
                first["source"]["baseline"]["evidence"]["capture_uri"],
                baseline_log.resolve().as_uri(),
            )
            self.assertEqual(
                first["source"]["candidate"]["evidence"]["capture_uri"],
                candidate_log.resolve().as_uri(),
            )
            self.assertEqual(
                first["profile"]["function"],
                "compare_recipe_reload_diagnostics",
            )
            command = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "tools/workbench.py"),
                    "runtime-diagnose",
                    str(project),
                    "--recipe-reload",
                    "--baseline-receipt",
                    str(baseline_receipt),
                    "--receipt",
                    str(candidate_receipt),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(command.returncode, 0, command.stderr)
            self.assertIn(
                "Cold-start Groovy conflict delta: MORE OBSERVED",
                command.stdout,
            )
            self.assertIn("+2 increased", command.stdout)
            self.assertIn("+1 newly-observed", command.stdout)
            self.assertIn("source revisions are not receipt-bound", command.stdout)

    def test_recipe_invalidation_feature_composes_verified_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _feature_recipe_checkpoint_fixture(
                root,
                "single",
                groovy_conflicts=1,
                java_duplicates=2,
            )

            first = diagnose_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )
            second = diagnose_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                launch_receipt=receipt,
            )

            self.assertEqual(first, second)
            self.assertEqual(
                first["format"],
                "workbench-supersymmetry-recipe-invalidation-diagnostic-v2",
            )
            self.assertEqual(first["profile"]["capability_maturity"], "preview")
            self.assertEqual(first["profile"]["runtime_support"], "provisional")
            self.assertNotIn("summary", first)
            self.assertEqual(
                first["channels"]["groovy_postinit"]["summary"]["complete_conflict_count"],
                1,
            )
            self.assertEqual(
                first["channels"]["gt_startup_registration"]["summary"]["complete_signal_count"],
                2,
            )
            schema = json.loads((
                REPOSITORY_ROOT
                / "profiles/packs/supersymmetry/diagnostics/recipe-invalidation-diagnostic-v2.schema.json"
            ).read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(first)

    def test_recipe_invalidation_feature_compares_without_cross_channel_sum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            baseline = _feature_recipe_checkpoint_fixture(
                root,
                "baseline-feature",
                groovy_conflicts=0,
                java_duplicates=1,
            )
            candidate = _feature_recipe_checkpoint_fixture(
                root,
                "candidate-feature",
                groovy_conflicts=1,
                java_duplicates=3,
            )

            result = compare_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                baseline_launch_receipt=baseline,
                candidate_launch_receipt=candidate,
            )

            self.assertEqual(result["state"], "more-observed")
            self.assertEqual(result["compatibility"]["state"], "comparable")
            self.assertNotIn("summary", result)
            self.assertEqual(
                result["channels"]["groovy_postinit"]["summary"]["candidate_complete_conflict_count"],
                1,
            )
            self.assertEqual(
                result["channels"]["gt_startup_registration"]["summary"]["candidate_complete_signal_count"],
                3,
            )
            command = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "tools/workbench.py"),
                    "runtime-diagnose",
                    str(project),
                    "--recipe-invalidations",
                    "--baseline-receipt",
                    str(baseline),
                    "--receipt",
                    str(candidate),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(command.returncode, 0, command.stderr)
            self.assertIn(
                "Recipe-registration signal delta: MORE OBSERVED",
                command.stdout,
            )
            self.assertIn("Groovy postInit: 1 (+1 vs explicit baseline 0)", command.stdout)
            self.assertIn("GT startup/latest.log: 3 (+2 vs explicit baseline 1)", command.stdout)
            self.assertIn("cross-log deduplication is unproven", command.stdout)

    def test_recipe_invalidation_validators_reject_rebound_profile_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            baseline_receipt = _feature_recipe_checkpoint_fixture(
                root,
                "baseline-custody",
                groovy_conflicts=0,
                java_duplicates=1,
            )
            candidate_receipt = _feature_recipe_checkpoint_fixture(
                root,
                "candidate-custody",
                groovy_conflicts=1,
                java_duplicates=2,
            )
            baseline = diagnose_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                launch_receipt=baseline_receipt,
            )
            candidate = diagnose_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                launch_receipt=candidate_receipt,
            )
            comparison = compare_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                baseline_launch_receipt=baseline_receipt,
                candidate_launch_receipt=candidate_receipt,
            )

            rebound_channel = deepcopy(
                baseline["channels"]["groovy_postinit"]
            )
            rebound_channel["source"]["evidence"]["sha256"] = "0" * 64
            rebound_channel["diagnostic_id"] = invalidation_runtime._identity(
                rebound_channel,
                "diagnostic_id",
            )
            with self.assertRaisesRegex(
                RuntimeRecipeInvalidationError,
                "invalid binding or identity",
            ):
                invalidation_runtime._validate_channel_report(
                    rebound_channel,
                    profile=baseline["profile"]["channels"]["groovy_postinit"],
                    source=baseline["channels"]["groovy_postinit"]["source"],
                )

            rebound_diagnostic = deepcopy(baseline)
            rebound_diagnostic["channels"]["groovy_postinit"] = rebound_channel
            rebound_diagnostic["diagnostic_id"] = invalidation_runtime._identity(
                rebound_diagnostic,
                "diagnostic_id",
            )
            with self.assertRaisesRegex(
                RuntimeRecipeInvalidationError,
                "invalid binding or identity",
            ):
                invalidation_runtime._validate_diagnostic(
                    rebound_diagnostic,
                    source=baseline["source"],
                    profile=baseline["profile"],
                    channels=baseline["channels"],
                )

            rebound_channel_comparison = deepcopy(
                comparison["channels"]["groovy_postinit"]
            )
            rebound_channel_comparison["source"]["baseline"]["evidence"][
                "sha256"
            ] = "0" * 64
            rebound_channel_comparison["comparison_id"] = (
                invalidation_runtime._identity(
                    rebound_channel_comparison,
                    "comparison_id",
                )
            )
            with self.assertRaisesRegex(
                RuntimeRecipeInvalidationError,
                "invalid binding or identity",
            ):
                invalidation_runtime._validate_channel_comparison(
                    rebound_channel_comparison,
                    profile=comparison["channels"]["groovy_postinit"]["profile"],
                    baseline=baseline["channels"]["groovy_postinit"],
                    candidate=candidate["channels"]["groovy_postinit"],
                )

            rebound_comparison = deepcopy(comparison)
            rebound_comparison["state"] = "release-ready"
            rebound_comparison["compatibility"] = {
                "state": "comparable",
                "findings": ["contradictory finding"],
            }
            rebound_comparison["channels"] = {
                "groovy_postinit": {},
                "gt_startup_registration": {},
            }
            rebound_comparison["comparison_id"] = invalidation_runtime._identity(
                rebound_comparison,
                "comparison_id",
            )
            with self.assertRaisesRegex(
                RuntimeRecipeInvalidationError,
                "invalid binding or identity",
            ):
                invalidation_runtime._validate_comparison(
                    rebound_comparison,
                    baseline=baseline,
                    candidate=candidate,
                    profile=comparison["profile"],
                    compatibility=comparison["compatibility"],
                    channels=comparison["channels"],
                )

    def test_recipe_invalidation_feature_returns_structured_incomparability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            baseline = _feature_recipe_checkpoint_fixture(
                root,
                "baseline-environment",
                groovy_conflicts=0,
                java_duplicates=0,
                java_runtime_id="sha256:" + ("7" * 64),
            )
            candidate = _feature_recipe_checkpoint_fixture(
                root,
                "candidate-environment",
                groovy_conflicts=0,
                java_duplicates=0,
                java_runtime_id="sha256:" + ("8" * 64),
            )

            result = compare_project_recipe_invalidations(
                REPOSITORY_ROOT,
                project,
                baseline_launch_receipt=baseline,
                candidate_launch_receipt=candidate,
            )

            self.assertEqual(result["state"], "incomparable")
            self.assertEqual(result["compatibility"]["state"], "incomparable")
            self.assertTrue(any(
                "java runtime" in finding.lower()
                for finding in result["compatibility"]["findings"]
            ))
            self.assertEqual(
                result["channels"]["groovy_postinit"],
                {"state": "not-compared"},
            )

    def test_recipe_invalidation_feature_requires_both_verified_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _v3_checkpoint_fixture(root)

            with self.assertRaisesRegex(
                RuntimeRecipeInvalidationError,
                "exactly one text-readable minecraft-groovy-log",
            ):
                diagnose_project_recipe_invalidations(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )

    def test_cold_start_comparison_rejects_reload_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            baseline_receipt, _log = _cold_recipe_checkpoint_fixture(
                root,
                "baseline",
                [],
            )
            candidate_root = root / "candidate"
            candidate_root.mkdir()
            candidate_receipt, _candidate_log = _recipe_checkpoint_fixture(
                candidate_root
            )

            with self.assertRaisesRegex(
                RuntimeRecipeDiagnosticError,
                "complete, untruncated initial postInit",
            ):
                compare_project_recipe_reload(
                    REPOSITORY_ROOT,
                    project,
                    baseline_launch_receipt=baseline_receipt,
                    candidate_launch_receipt=candidate_receipt,
                )

    def test_recipe_profile_rejects_a_different_pack_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            manifest = project / "pack.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'name = "Supersymmetry"',
                    'name = "Different Pack"',
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ProjectInspectionError,
                "does not match the selected pack profile",
            ):
                compare_project_recipe_reload(
                    REPOSITORY_ROOT,
                    project,
                    baseline_launch_receipt=root / "baseline.json",
                    candidate_launch_receipt=root / "candidate.json",
                )

    def test_recipe_reload_diagnostic_rejects_drifted_groovy_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, groovy_log = _recipe_checkpoint_fixture(root)
            groovy_log.write_text("changed\n", encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeDiagnosisError,
                "does not match its receipt",
            ):
                diagnose_project_recipe_reload(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )

    def test_recipe_reload_diagnostic_requires_groovy_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt = _v3_checkpoint_fixture(root)

            with self.assertRaisesRegex(
                RuntimeRecipeDiagnosticError,
                "exactly one text-readable minecraft-groovy-log",
            ):
                diagnose_project_recipe_reload(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )

    def test_rejects_evidence_that_drifted_after_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            receipt, crash, _jars = _launch_fixture(root)
            crash.write_text(CRASH + "changed\n", encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeDiagnosisError,
                "does not match its receipt",
            ):
                diagnose_project_runtime(
                    REPOSITORY_ROOT,
                    project,
                    launch_receipt=receipt,
                )


if __name__ == "__main__":
    unittest.main()
