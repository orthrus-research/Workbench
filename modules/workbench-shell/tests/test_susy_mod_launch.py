"""Focused tests for launching one retained SUSY constituent candidate."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/workbench-shell/src"
PROJECT_INTELLIGENCE = ROOT / "modules/project-intelligence/src"
ATLAS = ROOT / "modules/atlas/src"
BLUEPRINTS = ROOT / "modules/blueprints/src"

for path in (ATLAS, BLUEPRINTS, PROJECT_INTELLIGENCE, SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.path.insert(0, str(Path(__file__).parent))

from packwiz_v2_fixture import seal_packwiz_v2_receipt  # noqa: E402

from workbench_shell.susy_mod_dev import (  # noqa: E402
    RESULT_FORMAT,
    _digest,
    _runtime_tree,
)
from workbench_shell.susy_mod_launch import (  # noqa: E402
    SusyModLaunchError,
    _validate_retained_stage,
)
import workbench_shell.susy_mod_launch as susy_mod_launch  # noqa: E402
import workbench_shell.runtime_launch as runtime_launch  # noqa: E402


RUN_ID = "susy-mod-20260820T120000000000Z-abcdef123456"
PLAN_ID = "sha256:" + "2" * 64
MATERIALIZATION_ID = "sha256:" + "3" * 64
BASELINE = b"baseline-sample-jar"
CANDIDATE = b"candidate-sample-jar"
OWNED_JVM = {
    4242: {
        "pid": 4242,
        "creation_date": "20260820160000.000000-240",
        "executable_path": "C:/Java/bin/javaw.exe",
        "image": "javaw.exe",
    }
}
SUSY_RECCOMPLEX_CRASH = """\
---- Minecraft Crash Report ----
Description: Initializing game

net.minecraftforge.fml.common.LoaderExceptionModCrash: Caught exception from Recurrent Complex (reccomplex)
Caused by: java.lang.NoClassDefFoundError: ivorius/reccomplex/world/gen/feature/structure/context/StructureSpawnContext
Caused by: org.spongepowered.asm.mixin.injection.throwables.InjectionError: Critical injection failure: Variable modifier method setBlock(I)I in mixins.susy.reccomplex.json:StructureSpawnContextMixin from mod susy failed injection check, (0/1) succeeded. Scanned 0 target(s). Using refmap mixins.susy.refmap.json

Mixins in Stacktrace:
    ivorius/reccomplex/world/gen/feature/structure/context/StructureSpawnContext:
        supersymmetry.mixins.reccomplex.StructureSpawnContextMixin (mixins.susy.reccomplex.json) [susy]
"""


class _FakePopen:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _instance(root: Path, artifact: bytes) -> None:
    (root / ".minecraft/mods").mkdir(parents=True)
    (root / ".minecraft/config").mkdir(parents=True)
    (root / ".minecraft/mods/sample-1.0.jar").write_bytes(artifact)
    (root / ".minecraft/config/unrelated.cfg").write_text(
        "unchanged=true\n",
        encoding="utf-8",
    )
    (root / "instance.cfg").write_text(
        "InstanceType=OneSix\nManagedPack=false\nname=Supersymmetry\n",
        encoding="utf-8",
    )
    (root / "mmc-pack.json").write_text(
        '{"components":[{"uid":"net.minecraft","version":"1.12.2"}]}\n',
        encoding="utf-8",
    )


def _reseal_stage(stage: dict) -> None:
    stage_without_id = {
        key: value for key, value in stage.items() if key != "stage_id"
    }
    stage["stage_id"] = (
        "workbench-susy-mod-client-stage:" + _digest(stage_without_id)
    )


def _reseal_result(result: dict) -> None:
    result_without_id = {
        key: value for key, value in result.items() if key != "result_id"
    }
    result["result_id"] = (
        "workbench-susy-mod-dev-result:" + _digest(result_without_id)
    )


class _RetainedStage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.suite = root / "suite"
        self.run_root = self.suite / ".workbench/dev-runs" / RUN_ID
        self.canonical = root / "canonical/instance"
        self.staged = self.run_root / "runtime/client"
        _instance(self.canonical, BASELINE)
        self.staged.parent.mkdir(parents=True)
        shutil.copytree(self.canonical, self.staged)
        self.candidate_path = self.staged / ".minecraft/mods/sample-1.0.jar"
        self.candidate_path.write_bytes(CANDIDATE)

        source_summary, _source_records = _runtime_tree(
            self.canonical / ".minecraft"
        )
        target_summary, _target_records = _runtime_tree(
            self.staged / ".minecraft"
        )
        manifest_sha256 = sha256(
            (self.canonical / "mmc-pack.json").read_bytes()
        ).hexdigest()
        self.materialization_path = (
            root
            / "canonical/receipts"
            / "packwiz-materialization-v2.json"
        )
        self.materialization = {
            "format": "workbench-packwiz-materialization-receipt-v2",
            "schema_version": 2,
            "materialization_id": MATERIALIZATION_ID,
            "state": "materialized",
            "readiness": "pack-payload-installed",
            "payload": {
                **source_summary,
                "root_uri": (self.canonical / ".minecraft").as_uri(),
            },
            "launcher": {"manifest_sha256_after": manifest_sha256},
            "target": {
                "instance_root_uri": self.canonical.as_uri(),
                "receipt_uri": self.materialization_path.as_uri(),
            },
        }
        self.materialization = seal_packwiz_v2_receipt(
            self.materialization
        )
        self.materialization_id = self.materialization["materialization_id"]
        _write_json(self.materialization_path, self.materialization)

        self.stage = {
            "format": "workbench-susy-mod-client-stage-v1",
            "schema_version": 1,
            "state": "staged",
            "run_id": RUN_ID,
            "plan_id": PLAN_ID,
            "materialization_id": self.materialization_id,
            "source": {
                "instance_uri": self.canonical.as_uri(),
                "payload": source_summary,
                "receipt_uri": self.materialization_path.as_uri(),
            },
            "replacement": {
                "path": "mods/sample-1.0.jar",
                "baseline": {
                    "hash_format": "sha1",
                    "hash": sha1(BASELINE).hexdigest(),
                    "sha256": sha256(BASELINE).hexdigest(),
                    "size": len(BASELINE),
                },
                "candidate": {
                    "sha256": sha256(CANDIDATE).hexdigest(),
                    "size": len(CANDIDATE),
                    "mod_ids": ["sample"],
                },
            },
            "target": {
                "instance_uri": self.staged.as_uri(),
                "payload": target_summary,
                "changed_paths": ["mods/sample-1.0.jar"],
                "launch_ready": True,
                "launched": False,
            },
            "canonical_materialization_mutated": False,
        }
        _reseal_stage(self.stage)
        self.result = {
            "format": RESULT_FORMAT,
            "schema_version": 1,
            "run_id": RUN_ID,
            "outcome": "passed",
            "failed_stage": None,
            "plan_id": PLAN_ID,
            "artifact_set": [
                {
                    "path": str(self.run_root / "source/build/libs/sample.jar"),
                    "sha256": sha256(CANDIDATE).hexdigest(),
                    "size": len(CANDIDATE),
                    "mod_ids": ["sample"],
                }
            ],
            "runtime": {"client": deepcopy(self.stage), "server": None},
        }
        _reseal_result(self.result)
        self.write_records()

    def write_records(self) -> None:
        _write_json(self.run_root / "runtime/client-stage-v1.json", self.stage)
        _write_json(self.run_root / "result.json", self.result)

    def bind_stage_to_result(self) -> None:
        _reseal_stage(self.stage)
        self.result["runtime"]["client"] = deepcopy(self.stage)
        _reseal_result(self.result)
        self.write_records()


class SusyModLaunchStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = _RetainedStage(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self):
        return _validate_retained_stage(self.fixture.suite, RUN_ID)

    def test_exact_retained_stage_reopens_for_launch_without_mutation(self) -> None:
        before_stage = (
            self.fixture.run_root / "runtime/client-stage-v1.json"
        ).read_bytes()
        before_result = (self.fixture.run_root / "result.json").read_bytes()
        run_root, result, stage, staged, candidate = self.validate()

        self.assertEqual(run_root, self.fixture.run_root)
        self.assertEqual(result["result_id"], self.fixture.result["result_id"])
        self.assertEqual(stage["stage_id"], self.fixture.stage["stage_id"])
        self.assertEqual(staged, self.fixture.staged)
        self.assertEqual(candidate, self.fixture.candidate_path)
        self.assertEqual(
            (self.fixture.run_root / "runtime/client-stage-v1.json").read_bytes(),
            before_stage,
        )
        self.assertEqual(
            (self.fixture.run_root / "result.json").read_bytes(),
            before_result,
        )

    def test_retained_stage_keeps_materialization_receipt_unchanged(self) -> None:
        receipt_before = self.fixture.materialization_path.read_bytes()

        _run_root, _result, stage, _staged, _candidate = (
            _validate_retained_stage(self.fixture.suite, RUN_ID)
        )

        self.assertEqual(
            stage["source"]["receipt_uri"],
            self.fixture.materialization_path.as_uri(),
        )
        self.assertEqual(
            self.fixture.materialization_path.read_bytes(),
            receipt_before,
        )

    def test_candidate_or_unrelated_staged_payload_drift_is_rejected(self) -> None:
        cases = (
            (
                "candidate",
                lambda fixture: fixture.candidate_path.write_bytes(b"tampered"),
            ),
            (
                "unrelated-config",
                lambda fixture: (
                    fixture.staged / ".minecraft/config/unrelated.cfg"
                ).write_text("drifted=true\n", encoding="utf-8"),
            ),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = _RetainedStage(Path(temporary))
                    mutate(fixture)
                    with self.assertRaisesRegex(
                        SusyModLaunchError,
                        "payload has drifted",
                    ):
                        _validate_retained_stage(fixture.suite, RUN_ID)

    def test_public_launch_rejects_stage_drift_before_launcher_probe(self) -> None:
        self.fixture.candidate_path.write_bytes(b"tampered-before-launch")

        with patch.object(susy_mod_launch, "_probe_launcher") as launcher_probe:
            with self.assertRaisesRegex(
                SusyModLaunchError,
                "payload has drifted",
            ):
                susy_mod_launch.launch_susy_mod_client(
                    self.fixture.suite,
                    RUN_ID,
                    launcher_executable=self.fixture.root / "launcher",
                    launcher_root=self.fixture.root / "launcher-root",
                )

        launcher_probe.assert_not_called()
        self.assertFalse((self.fixture.run_root / "runtime/launches").exists())
        self.assertFalse(
            (self.fixture.run_root / "runtime/client-launch.lock").exists()
        )

    def test_public_launch_rejects_unresponsive_launcher_before_projection(
        self,
    ) -> None:
        executable = self.fixture.root / "prismlauncher.exe"
        launcher_root = self.fixture.root / "launcher-root"
        launcher_process = {
            32040: {
                "pid": 32040,
                "creation_date": "20260820162800.000000-240",
                "executable_path": "C:/PrismLauncher/prismlauncher.exe",
                "responding": False,
            }
        }
        with (
            patch.object(
                susy_mod_launch,
                "_probe_launcher",
                return_value=(
                    executable,
                    {"sha256": "1" * 64, "size": 1},
                    {"os": "windows", "architecture": "x64"},
                ),
            ),
            patch.object(
                susy_mod_launch,
                "_windows_launcher_processes",
                return_value=launcher_process,
            ),
            patch.object(
                susy_mod_launch,
                "_launcher_path",
                return_value="C:/PrismLauncher/prismlauncher.exe",
            ) as launcher_path,
            patch.object(
                susy_mod_launch,
                "ensure_java_runtime",
            ) as ensure_java,
            patch.object(
                susy_mod_launch,
                "_runtime_tree",
                wraps=susy_mod_launch._runtime_tree,
            ) as runtime_tree,
        ):
            with self.assertRaisesRegex(
                SusyModLaunchError,
                "launcher process is not responding",
            ):
                susy_mod_launch.launch_susy_mod_client(
                    self.fixture.suite,
                    RUN_ID,
                    launcher_executable=executable,
                    launcher_root=launcher_root,
                )

        launcher_path.assert_called_once_with(executable, {"os": "windows", "architecture": "x64"})
        ensure_java.assert_not_called()
        self.assertEqual(runtime_tree.call_count, 2)
        self.assertFalse((self.fixture.run_root / "runtime/launches").exists())
        self.assertFalse(
            (self.fixture.run_root / "runtime/client-launch.lock").exists()
        )

    def test_public_cross_host_launch_rejects_existing_launcher_before_projection(
        self,
    ) -> None:
        executable = self.fixture.root / "prismlauncher.exe"
        launcher_root = self.fixture.root / "launcher-root"
        launcher_process = {
            32040: {
                "pid": 32040,
                "creation_date": "20260820162800.000000-240",
                "executable_path": "C:/PrismLauncher/prismlauncher.exe",
                "responding": True,
            }
        }
        with (
            patch.object(
                susy_mod_launch,
                "_probe_launcher",
                return_value=(
                    executable,
                    {"sha256": "1" * 64, "size": 1},
                    {"os": "windows", "architecture": "x64"},
                ),
            ),
            patch.object(
                susy_mod_launch,
                "_windows_launcher_processes",
                return_value=launcher_process,
            ),
            patch.object(
                susy_mod_launch,
                "_launcher_path",
                return_value="C:/PrismLauncher/prismlauncher.exe",
            ) as launcher_path,
            patch.object(
                susy_mod_launch,
                "host_platform",
                return_value={"os": "linux", "architecture": "x64"},
            ),
            patch.object(
                susy_mod_launch,
                "ensure_java_runtime",
            ) as ensure_java,
        ):
            with self.assertRaisesRegex(
                SusyModLaunchError,
                "prevents reliable cross-host launch custody",
            ):
                susy_mod_launch.launch_susy_mod_client(
                    self.fixture.suite,
                    RUN_ID,
                    launcher_executable=executable,
                    launcher_root=launcher_root,
                )

        launcher_path.assert_called_once_with(executable, {"os": "windows", "architecture": "x64"})
        ensure_java.assert_not_called()
        self.assertFalse((self.fixture.run_root / "runtime/launches").exists())
        self.assertFalse(
            (self.fixture.run_root / "runtime/client-launch.lock").exists()
        )

    def test_cleanup_reaps_exact_instance_launcher_process(self) -> None:
        launcher = {
            27036: {
                "pid": 27036,
                "creation_date": "20260821165056.000000-240",
                "executable_path": "C:/PrismLauncher/prismlauncher.exe",
                "responding": True,
            }
        }
        with (
            patch.object(
                susy_mod_launch,
                "_windows_owned_processes",
                return_value={},
            ),
            patch.object(
                susy_mod_launch,
                "_windows_instance_launcher_processes",
                side_effect=[launcher, {}, {}, {}],
            ) as instance_launchers,
            patch.object(
                susy_mod_launch,
                "_windows_process_action",
            ) as process_action,
        ):
            cleanup = susy_mod_launch._cleanup_owned_processes(
                None,
                launcher_host={"os": "windows", "architecture": "x64"},
                instance_id="candidate-load-nonce",
                observed_windows={},
                launcher_executable="C:/PrismLauncher/prismlauncher.exe",
                launcher_instance_id="workbench-supersymmetry-exact-instance",
            )

        self.assertFalse(cleanup["owned_processes_running"])
        self.assertEqual(cleanup["errors"], [])
        self.assertEqual(cleanup["launcher"]["tracked"], [launcher[27036]])
        self.assertTrue(cleanup["launcher"]["graceful"])
        self.assertTrue(cleanup["launcher"]["empty"])
        process_action.assert_any_call(launcher, force=False)
        instance_launchers.assert_any_call(
            "C:/PrismLauncher/prismlauncher.exe",
            "workbench-supersymmetry-exact-instance",
        )

    def test_cleanup_force_revalidates_launcher_identity_not_ui_state(self) -> None:
        tracked = {
            27036: {
                "pid": 27036,
                "creation_date": "20260821165056.000000-240",
                "executable_path": "C:/PrismLauncher/prismlauncher.exe",
                "responding": True,
            }
        }
        stopped_responding = deepcopy(tracked)
        stopped_responding[27036]["responding"] = False
        with (
            patch.object(
                susy_mod_launch,
                "_windows_owned_processes",
                return_value={},
            ),
            patch.object(
                susy_mod_launch,
                "_windows_instance_launcher_processes",
                side_effect=[tracked, stopped_responding, {}],
            ),
            patch.object(
                susy_mod_launch,
                "_wait_windows_instance_launcher_empty",
                side_effect=[False, True],
            ),
            patch.object(
                susy_mod_launch,
                "_windows_process_action",
            ) as process_action,
        ):
            cleanup = susy_mod_launch._cleanup_owned_processes(
                None,
                launcher_host={"os": "windows", "architecture": "x64"},
                instance_id="candidate-load-nonce",
                observed_windows={},
                launcher_executable="C:/PrismLauncher/prismlauncher.exe",
                launcher_instance_id="workbench-supersymmetry-exact-instance",
            )

        process_action.assert_any_call(stopped_responding, force=True)
        self.assertTrue(cleanup["launcher"]["forced"])
        self.assertTrue(cleanup["launcher"]["empty"])
        self.assertFalse(cleanup["owned_processes_running"])

    def test_unknown_or_repeated_runtime_experiment_fails_before_launcher_probe(
        self,
    ) -> None:
        cases = (
            ("unknown", ["nearby-unreviewed-patch"]),
            (
                "repeated",
                ["susy-reccomplex-arg3", "susy-reccomplex-arg3"],
            ),
        )
        for label, experiments in cases:
            with self.subTest(label=label):
                with patch.object(
                    susy_mod_launch,
                    "_probe_launcher",
                ) as launcher_probe:
                    with self.assertRaisesRegex(
                        SusyModLaunchError,
                        "unknown or repeated",
                    ):
                        susy_mod_launch.launch_susy_mod_client(
                            self.fixture.suite,
                            RUN_ID,
                            launcher_executable=self.fixture.root / "launcher",
                            launcher_root=self.fixture.root / "launcher-root",
                            compatibility_experiments=experiments,
                        )

                launcher_probe.assert_not_called()
                self.assertFalse(
                    (self.fixture.run_root / "runtime/launches").exists()
                )
                self.assertFalse(
                    (
                        self.fixture.run_root
                        / "runtime/client-launch.lock"
                    ).exists()
                )

    def test_canonical_payload_and_baseline_drift_are_rejected(self) -> None:
        cases = (
            (
                "canonical-config",
                lambda fixture: (
                    fixture.canonical / ".minecraft/config/unrelated.cfg"
                ).write_text("drifted=true\n", encoding="utf-8"),
                "payload has drifted",
            ),
            (
                "baseline-jar",
                lambda fixture: (
                    fixture.canonical / ".minecraft/mods/sample-1.0.jar"
                ).write_bytes(b"different-baseline"),
                "payload has drifted",
            ),
        )
        for label, mutate, message in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = _RetainedStage(Path(temporary))
                    mutate(fixture)
                    with self.assertRaisesRegex(SusyModLaunchError, message):
                        _validate_retained_stage(fixture.suite, RUN_ID)

    def test_staged_launcher_base_drift_is_rejected(self) -> None:
        (self.fixture.staged / "instance.cfg").write_text(
            "name=forged\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            SusyModLaunchError,
            "base differs",
        ):
            self.validate()

    def test_materialization_payload_root_tampering_is_rejected(self) -> None:
        other = self.fixture.root / "other-payload"
        other.mkdir()
        self.fixture.materialization["payload"]["root_uri"] = other.as_uri()
        _write_json(
            self.fixture.materialization_path,
            self.fixture.materialization,
        )

        with self.assertRaisesRegex(
            SusyModLaunchError,
            "materialization receipt does not bind the stage",
        ):
            self.validate()

    def test_receipt_resealing_cannot_authorize_extra_staged_changes(self) -> None:
        extra = self.fixture.staged / ".minecraft/config/extra.cfg"
        extra.write_text("extra=true\n", encoding="utf-8")
        target_summary, _records = _runtime_tree(
            self.fixture.staged / ".minecraft"
        )
        self.fixture.stage["target"]["payload"] = target_summary
        self.fixture.stage["target"]["changed_paths"] = [
            "config/extra.cfg",
            "mods/sample-1.0.jar",
        ]
        self.fixture.bind_stage_to_result()

        with self.assertRaisesRegex(
            SusyModLaunchError,
            "replacement identity is invalid|changes more than",
        ):
            self.validate()

    def test_result_and_stage_must_retain_the_same_exact_receipt(self) -> None:
        self.fixture.stage["replacement"]["candidate"]["mod_ids"] = ["other"]
        _reseal_stage(self.fixture.stage)
        self.fixture.write_records()

        with self.assertRaisesRegex(
            SusyModLaunchError,
            "not a passing client stage",
        ):
            self.validate()

    def test_invalid_or_escaping_run_id_is_rejected(self) -> None:
        for run_id in (
            "../" + RUN_ID,
            RUN_ID + "/child",
            "latest",
            "susy-mod-20260820T120000000000Z-ABCDEF123456",
        ):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(
                    SusyModLaunchError,
                    "exact retained SUSY run ID",
                ):
                    _validate_retained_stage(self.fixture.suite, run_id)


@unittest.skipUnless(
    hasattr(susy_mod_launch, "_validate_loaded_source_proof"),
    "loaded-source proof validator is being implemented",
)
class SusyModLaunchProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "projection/.minecraft/mods/sample-1.0.jar"
        self.candidate.parent.mkdir(parents=True)
        self.candidate.write_bytes(CANDIDATE)
        self.proof_path = self.root / "candidate-loaded-proof.json"
        self.nonce = "launch-nonce-0123456789abcdef"
        self.launcher_host = {
            "os": "linux",
            "architecture": "x64",
            "system": "Linux",
            "machine": "x86_64",
        }
        self.proof = {
            "format": "workbench-forge-loaded-source-probe-v1",
            "nonce": self.nonce,
            "process": "4242@fixture-host",
            "pid": 4242,
            "mods": [
                {
                    "mod_id": "sample",
                    "version": "1.0",
                    "source_path": str(self.candidate.resolve()),
                    "sha256": sha256(CANDIDATE).hexdigest(),
                    "size": len(CANDIDATE),
                }
            ],
        }
        self.write_proof()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_proof(self) -> None:
        _write_json(self.proof_path, self.proof)

    def validate(self):
        validator = susy_mod_launch._validate_loaded_source_proof
        return validator(
            self.proof_path,
            nonce=self.nonce,
            expected_mod_ids=["sample"],
            expected_source_path=self.candidate,
            expected_sha256=sha256(CANDIDATE).hexdigest(),
            expected_size=len(CANDIDATE),
            launcher_host=self.launcher_host,
        )

    def test_exact_nonce_mod_source_and_bytes_are_accepted(self) -> None:
        validated = self.validate()

        self.assertEqual(validated["nonce"], self.nonce)
        self.assertEqual(validated["mods"][0]["mod_id"], "sample")
        self.assertEqual(
            validated["mods"][0]["sha256"],
            sha256(CANDIDATE).hexdigest(),
        )

    def test_nearby_or_replayed_proofs_are_rejected(self) -> None:
        cases = {
            "wrong-nonce": lambda proof: proof.update(nonce="prior-launch"),
            "pid-process-mismatch": lambda proof: proof.update(
                process="9999@fixture-host"
            ),
            "wrong-source": lambda proof: proof["mods"][0].update(
                source_path=str((self.root / "baseline.jar").resolve())
            ),
            "wrong-digest": lambda proof: proof["mods"][0].update(
                sha256="f" * 64
            ),
            "wrong-size": lambda proof: proof["mods"][0].update(
                size=len(CANDIDATE) + 1
            ),
            "wrong-mod": lambda proof: proof["mods"][0].update(
                mod_id="different"
            ),
            "missing-mod": lambda proof: proof.update(mods=[]),
            "extra-mod": lambda proof: proof["mods"].append(
                {
                    "mod_id": "unrelated",
                    "version": "1.0",
                    "source_path": str(self.candidate.resolve()),
                    "sha256": sha256(CANDIDATE).hexdigest(),
                    "size": len(CANDIDATE),
                }
            ),
            "duplicate-mod": lambda proof: proof["mods"].append(
                deepcopy(proof["mods"][0])
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                original = deepcopy(self.proof)
                mutate(self.proof)
                self.write_proof()
                with self.assertRaisesRegex(SusyModLaunchError, "proof"):
                    self.validate()
                self.proof = original

    def test_malformed_or_linked_proof_is_rejected(self) -> None:
        self.proof_path.write_text("not-json\n", encoding="utf-8")
        with self.assertRaisesRegex(SusyModLaunchError, "invalid JSON"):
            self.validate()

        self.proof_path.unlink()
        target = self.root / "other-proof.json"
        _write_json(target, self.proof)
        self.proof_path.symlink_to(target)
        with self.assertRaisesRegex(SusyModLaunchError, "regular file"):
            self.validate()


class SusyModLaunchCrashDiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.crash = self.root / "crash-susy-reccomplex-client.txt"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_retained_susy_reccomplex_shape_names_explicit_experiment(
        self,
    ) -> None:
        self.crash.write_text(SUSY_RECCOMPLEX_CRASH, encoding="utf-8")

        diagnosis = susy_mod_launch._diagnose_crash(self.crash, ROOT)

        self.assertIsNotNone(diagnosis)
        assert diagnosis is not None
        self.assertEqual(
            diagnosis["pattern_id"],
            "workbench-pack:supersymmetry:"
            "susy-reccomplex-named-variable-v1",
        )
        self.assertEqual(
            diagnosis["category"],
            "mixin-local-variable-discriminator",
        )
        self.assertIn("arg3", diagnosis["interpretation"])
        self.assertIn("flag", diagnosis["interpretation"])
        self.assertGreaterEqual(len(diagnosis["developer_actions"]), 2)
        experiment = diagnosis["available_experiment"]
        self.assertEqual(experiment["id"], "susy-reccomplex-arg3")
        self.assertEqual(
            experiment["scope"],
            "disposable launcher projection only",
        )
        spec = Path(experiment["spec_uri"].removeprefix("file://"))
        self.assertEqual(
            sha256(spec.read_bytes()).hexdigest(),
            experiment["spec_sha256"],
        )
        self.assertEqual(spec.stat().st_size, experiment["spec_size"])

    def test_nearby_crash_shapes_do_not_select_the_experiment(self) -> None:
        cases = {
            "different-mixin": (
                "mixins.susy.reccomplex.json:StructureSpawnContextMixin",
                "mixins.susy.reccomplex.json:DifferentContextMixin",
            ),
            "different-target": (
                "StructureSpawnContext",
                "NearbyStructureContext",
            ),
            "different-method": ("setBlock(I)I", "setBlock(II)I"),
            "different-failure": ("InjectionError", "InvalidInjectionException"),
        }
        for label, (required, nearby) in cases.items():
            with self.subTest(label=label):
                self.crash.write_text(
                    SUSY_RECCOMPLEX_CRASH.replace(required, nearby),
                    encoding="utf-8",
                )
                self.assertIsNone(
                    susy_mod_launch._diagnose_crash(self.crash, ROOT)
                )

    def test_current_susycore_crash_selects_only_the_exact_0112_overlay(self) -> None:
        self.crash.write_text(SUSY_RECCOMPLEX_CRASH, encoding="utf-8")
        runtime_records = {
            "mods/supersymmetry-v0.1.112.jar": {
                "sha256": "11101461f859ccae3f9924ea0903c02694e52e7cb93048e7fc89adbda70bf3ec"
            },
            "mods/RecurrentComplex-1.4.8.6.jar": {
                "sha256": "253226e6c7efe61ae255df0cc2e19d1420945cb7e86f7cd79f51d5db10fd9de8"
            },
        }

        diagnosis = susy_mod_launch._diagnose_crash(
            self.crash,
            ROOT,
            pack_version="0.1.16.12",
            runtime_records=runtime_records,
        )

        self.assertIsNotNone(diagnosis)
        assert diagnosis is not None
        self.assertEqual(
            "workbench-pack:supersymmetry:"
            "susy-reccomplex-named-variable-0112-v1",
            diagnosis["pattern_id"],
        )
        experiment = diagnosis["available_experiment"]
        self.assertEqual(
            "susy-reccomplex-susycore-0112-flag", experiment["id"]
        )
        self.assertEqual("0.1.16.12", experiment["applicability"]["pack_version"])
        self.assertEqual("0.1.112", experiment["applicability"]["susycore"])

        with self.assertRaisesRegex(
            SusyModLaunchError, "applies to Supersymmetry 0.1.16.11"
        ):
            susy_mod_launch._compatibility_experiment_scopes(
                ROOT,
                ("susy-reccomplex-arg3",),
                pack_version="0.1.16.12",
                runtime_records=runtime_records,
            )

        suite = self.root / "suite"
        for relative in (
            "profiles/packs/supersymmetry/profile.yaml",
            "profiles/packs/supersymmetry/compatibility/"
            "susycore-0.1.112-reccomplex-flag-v1.json",
            "profiles/packs/supersymmetry/atlas/runtime-graph/"
            "cleanroom-compatibility-overlays-v1.json",
        ):
            source = ROOT / relative
            target = suite / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        authority_path = (
            suite
            / "profiles/packs/supersymmetry/atlas/runtime-graph/"
            "cleanroom-compatibility-overlays-v1.json"
        )
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        authority["format"] = "not-the-overlay-authority"
        authority_path.write_text(
            json.dumps(authority, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            SusyModLaunchError, "overlay authority has the wrong identity"
        ):
            susy_mod_launch._compatibility_experiment_scopes(
                suite,
                ("susy-reccomplex-susycore-0112-flag",),
                pack_version="0.1.16.12",
                runtime_records=runtime_records,
            )


class SusyModLaunchObservationBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.projection = self.root / "projection"
        runtime = self.projection / ".minecraft"
        (runtime / "logs").mkdir(parents=True)
        (runtime / "crash-reports").mkdir()
        (runtime / "logs/latest.log").write_text(
            "Forge Mod Loader has successfully loaded 188 mods\n",
            encoding="utf-8",
        )
        (runtime / "crash-reports/crash-stale.txt").write_text(
            "stale crash\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stale_logs_and_crashes_are_removed_only_from_projection(self) -> None:
        retained = susy_mod_launch._clear_projection_runtime_evidence(
            self.projection
        )

        self.assertEqual(
            [row["path"] for row in retained],
            ["logs", "crash-reports"],
        )
        self.assertFalse((self.projection / ".minecraft/logs").exists())
        self.assertFalse(
            (self.projection / ".minecraft/crash-reports").exists()
        )
        self.assertEqual(
            retained[0]["identity"]["file_count"],
            1,
        )

    def test_real_fml_examination_line_corroborates_candidate(self) -> None:
        debug = self.projection / ".minecraft/logs/debug.log"
        debug.write_text(
            "[Client thread/DEBUG] [FML]: "
            "Examining file sample-1.0.jar for potential mods\n"
            "[Client thread/DEBUG] [FML]: "
            "Identified mod sample; loading\n",
            encoding="utf-8",
        )

        record = susy_mod_launch._loader_log_corroboration(
            debug,
            "sample-1.0.jar",
            ["sample"],
        )

        self.assertTrue(record["passed"])
        self.assertTrue(record["discovery"])
        self.assertEqual(record["mod_ids"], {"sample": True})

    def test_total_loader_marker_or_arbitrary_filename_text_is_not_corroboration(
        self,
    ) -> None:
        debug = self.projection / ".minecraft/logs/debug.log"
        debug.write_text(
            "sample-1.0.jar sample\n"
            "Forge Mod Loader has successfully loaded 188 mods\n",
            encoding="utf-8",
        )

        record = susy_mod_launch._loader_log_corroboration(
            debug,
            "sample-1.0.jar",
            ["sample"],
        )

        self.assertFalse(record["passed"])
        self.assertFalse(record["discovery"])


class SusyModLaunchRenderTests(unittest.TestCase):
    def test_human_failure_renders_cleanup_error_reason_and_diagnosis(self) -> None:
        result = {
            "outcome": "failed",
            "receipt": {
                "run_id": RUN_ID,
                "candidate": {"path": "mods/sample.jar", "sha256": "1" * 64},
                "loaded_source_proof": None,
                "failure_kind": "cleanup-incomplete",
                "reason": "owned process inventory could not be revalidated",
                "cleanup": {
                    "owned_processes_running": False,
                    "errors": ["custody query failed"],
                },
                "diagnosis": {
                    "interpretation": "The exact named-variable bridge is missing.",
                    "available_experiment": {"id": "susy-reccomplex-arg3"},
                },
                "target": {"receipt_uri": "file:///retained/receipt.json"},
            },
            "next_actions": [],
        }

        rendered = susy_mod_launch.render_susy_mod_launch(result)

        self.assertIn("Cleanup: incomplete", rendered)
        self.assertIn(
            "Reason: owned process inventory could not be revalidated",
            rendered,
        )
        self.assertIn(
            "Diagnosis: The exact named-variable bridge is missing.",
            rendered,
        )
        self.assertIn("Available experiment: susy-reccomplex-arg3", rendered)


class SusyModLaunchProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "retained-instance"
        self.instances = self.root / "launcher/instances"
        self.instances.mkdir(parents=True)
        _instance(self.source, CANDIDATE)
        self.payload = runtime_launch._regular_tree_identity(
            self.source / ".minecraft",
            include_directories=False,
            include_modes=True,
        )
        self.probe_jar = self.root / "probe-build/probe.jar"
        self.probe_jar.parent.mkdir()
        self.probe_jar.write_bytes(b"disposable-java-agent")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def project(
        self,
        destination: Path,
        *,
        additional_files: dict[str, Path] | None = None,
        additional_config: dict[str, str] | None = None,
    ) -> dict:
        return runtime_launch._project_instance(
            self.source,
            destination,
            expected_payload=self.payload,
            java_path="/managed-jdk/bin/java",
            java_probe={"os_arch": "amd64", "java_version": "17.0.12"},
            display_name="Workbench SUSY Dev",
            memory_mib=8192,
            additional_files=additional_files,
            additional_config=additional_config,
        )

    def test_probe_jar_and_jvm_configuration_exist_before_atomic_publish(
        self,
    ) -> None:
        destination = self.instances / "susy-candidate"
        probe_relative = (
            ".workbench/candidate-loaded-probe/"
            "workbench-candidate-loaded-agent.jar"
        )
        javaagent = (
            "-javaagent:/launcher/instances/susy-candidate/"
            + probe_relative
            + "=nonce:arguments"
        )
        source_config_before = (self.source / "instance.cfg").read_bytes()
        original_rename = Path.rename
        publication_observations: list[dict[str, object]] = []

        def observe_publish(staged: Path, published: Path) -> Path:
            config = (staged / "instance.cfg").read_text(encoding="utf-8")
            publication_observations.append({
                "published_absent": not published.exists(),
                "staged_outside_instances": self.instances not in staged.parents,
                "probe": (staged / probe_relative).read_bytes(),
                "jvm_args": f"JvmArgs={javaagent}\n" in config,
                "override": "OverrideJavaArgs=true\n" in config,
            })
            return original_rename(staged, published)

        with patch.object(Path, "rename", autospec=True, side_effect=observe_publish):
            record = self.project(
                destination,
                additional_files={probe_relative: self.probe_jar},
                additional_config={
                    "JvmArgs": javaagent,
                    "OverrideJavaArgs": "true",
                },
            )

        self.assertEqual(
            publication_observations,
            [{
                "published_absent": True,
                "staged_outside_instances": True,
                "probe": b"disposable-java-agent",
                "jvm_args": True,
                "override": True,
            }],
        )
        self.assertEqual(
            (destination / probe_relative).read_bytes(),
            b"disposable-java-agent",
        )
        projected_config = (destination / "instance.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"JvmArgs={javaagent}\n", projected_config)
        self.assertIn("OverrideJavaArgs=true\n", projected_config)
        self.assertEqual(
            record["configuration"]["settings"]["JvmArgs"],
            javaagent,
        )
        self.assertEqual(
            record["added_files"],
            [{
                "path": probe_relative,
                "sha256": sha256(b"disposable-java-agent").hexdigest(),
                "size": len(b"disposable-java-agent"),
            }],
        )
        self.assertEqual(
            (self.source / "instance.cfg").read_bytes(),
            source_config_before,
        )
        self.assertFalse((self.source / probe_relative).exists())

    def test_additional_file_paths_cannot_escape_projection(self) -> None:
        unsafe_paths = (
            "",
            "../probe.jar",
            "/absolute/probe.jar",
            "nested\\probe.jar",
        )
        for index, relative in enumerate(unsafe_paths):
            destination = self.instances / f"unsafe-{index}"
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(
                    runtime_launch.RuntimeLaunchError,
                    "launcher projection file path is unsafe",
                ):
                    self.project(
                        destination,
                        additional_files={relative: self.probe_jar},
                    )
                self.assertFalse(destination.exists())

    def test_additional_file_source_cannot_be_a_symbolic_link(self) -> None:
        destination = self.instances / "linked-probe"
        linked_probe = self.root / "linked-probe.jar"
        linked_probe.symlink_to(self.probe_jar)

        with self.assertRaisesRegex(
            runtime_launch.RuntimeLaunchError,
            "launcher projection file is unavailable",
        ):
            self.project(
                destination,
                additional_files={".workbench/probe.jar": linked_probe},
            )

        self.assertFalse(destination.exists())

    def test_failed_prepublish_mutation_never_exposes_an_instance(self) -> None:
        destination = self.instances / "failed-prepublish"
        source_config_before = (self.source / "instance.cfg").read_bytes()

        def fail_after_mutation(staged: Path) -> None:
            self.assertFalse(destination.exists())
            self.assertNotIn(self.instances, staged.parents)
            (staged / ".minecraft/config/prepared.cfg").write_text(
                "prepared=true\n",
                encoding="utf-8",
            )
            raise runtime_launch.RuntimeLaunchError("prepublish probe failed")

        with self.assertRaisesRegex(
            runtime_launch.RuntimeLaunchError,
            "prepublish probe failed",
        ):
            runtime_launch._project_instance(
                self.source,
                destination,
                expected_payload=self.payload,
                java_path="/managed-jdk/bin/java",
                java_probe={"os_arch": "amd64", "java_version": "17.0.12"},
                display_name="Workbench SUSY Dev",
                memory_mib=8192,
                prepare_projection=fail_after_mutation,
            )

        self.assertFalse(destination.exists())
        self.assertFalse(
            (self.source / ".minecraft/config/prepared.cfg").exists()
        )
        self.assertEqual(
            (self.source / "instance.cfg").read_bytes(),
            source_config_before,
        )

    def test_additional_file_cannot_replace_materialized_payload(self) -> None:
        destination = self.instances / "payload-collision"

        with self.assertRaisesRegex(
            runtime_launch.RuntimeLaunchError,
            "launcher projection file already exists",
        ):
            self.project(
                destination,
                additional_files={
                    ".minecraft/mods/sample-1.0.jar": self.probe_jar,
                },
            )

        self.assertFalse(destination.exists())
        self.assertEqual(
            (self.source / ".minecraft/mods/sample-1.0.jar").read_bytes(),
            CANDIDATE,
        )

    def test_additional_configuration_cannot_override_core_setting(self) -> None:
        destination = self.instances / "config-collision"

        with self.assertRaisesRegex(
            runtime_launch.RuntimeLaunchError,
            "additional launcher configuration overrides a core setting",
        ):
            self.project(
                destination,
                additional_config={"JavaPath": "/attacker-controlled/java"},
            )

        self.assertFalse(destination.exists())


class SusyModLaunchMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.projection = Path(self.temporary.name) / "projection"
        (self.projection / ".minecraft/logs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def monitor(self, process: _FakePopen, *, timeout: float = 0.05):
        return susy_mod_launch._monitor_susy_launch(
            process,
            self.projection,
            custody_token="nonce-bound-instance-token",
            timeout_seconds=timeout,
            poll_interval_seconds=0.001,
        )

    def test_nonce_bound_game_jvm_exit_fails_immediately(self) -> None:
        with patch.object(
            susy_mod_launch,
            "_windows_owned_processes",
            side_effect=[deepcopy(OWNED_JVM), {}],
        ) as inventory:
            observation, seen = self.monitor(_FakePopen(0), timeout=1.0)

        self.assertEqual(observation["outcome"], "failed")
        self.assertEqual(
            observation["failure_kind"],
            "game-jvm-exited-before-checkpoint",
        )
        self.assertEqual(observation["process_state"], "exited")
        self.assertEqual(seen, OWNED_JVM)
        self.assertEqual(inventory.call_count, 2)

    def test_terminal_foundation_or_mixin_marker_fails_startup(self) -> None:
        markers = (
            "[main/FATAL] [Foundation]: Unable to launch",
            "org.spongepowered.asm.mixin.transformer.throwables."
            "MixinTargetAlreadyLoadedException: critical target was loaded",
            "net.minecraftforge.fml.common.MissingModsException: "
            "Mod rtg requires [biomesoplenty@[7.0.1.2441,)]",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                debug = self.projection / ".minecraft/logs/debug.log"
                debug.write_text(marker + "\n", encoding="utf-8")
                with patch.object(
                    susy_mod_launch,
                    "_windows_owned_processes",
                    return_value=deepcopy(OWNED_JVM),
                ):
                    observation, seen = self.monitor(_FakePopen(None))

                self.assertEqual(observation["outcome"], "failed")
                self.assertEqual(
                    observation["failure_kind"],
                    "fatal-startup-log",
                )
                self.assertIn(
                    observation["marker"],
                    marker,
                )
                self.assertEqual(seen, OWNED_JVM)

    def test_fml_checkpoint_without_live_nonce_bound_jvm_cannot_pass(self) -> None:
        latest = self.projection / ".minecraft/logs/latest.log"
        latest.write_text(
            "[Client thread/INFO] [FML]: "
            "Forge Mod Loader has successfully loaded 188 mods\n",
            encoding="utf-8",
        )
        with patch.object(
            susy_mod_launch,
            "_windows_owned_processes",
            return_value={},
        ):
            observation, seen = self.monitor(_FakePopen(0), timeout=0.01)

        self.assertEqual(observation["outcome"], "failed")
        self.assertEqual(
            observation["failure_kind"],
            "game-jvm-never-started",
        )
        self.assertNotIn("checkpoint", observation)
        self.assertEqual(seen, {})


if __name__ == "__main__":
    unittest.main()
