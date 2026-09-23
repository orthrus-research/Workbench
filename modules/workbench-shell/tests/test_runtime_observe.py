"""Focused tests for close-triggered runtime observation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.runtime_observe import (  # noqa: E402
    RuntimeObserveError,
    _capture_final_evidence,
    _observe_process_exit,
    _windows_runtime_pids,
    preflight_close_observer_launcher,
    require_runtime_processes_closed,
    observe_project_runtime,
)
from workbench_shell.runtime_anvil_observe import (  # noqa: E402
    RuntimeAnvilObserveError,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _launch_fixture(
    root: Path,
    *,
    state_root: Path | None = None,
) -> tuple[dict[str, object], Path, Path]:
    projection = root / "projection"
    runtime = projection / ".minecraft"
    logs = runtime / "logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text(
        "Forge Mod Loader has successfully loaded 210 mods\n"
        "[Client thread/INFO] [net.minecraft.client.Minecraft]: Stopping!\n",
        encoding="utf-8",
    )
    (logs / "debug.log").write_text("debug\n", encoding="utf-8")
    state = root / ".workbench" if state_root is None else state_root
    run_root = state / "evidence/runtime/test/launches/instance"
    run_root.mkdir(parents=True)
    receipt_path = run_root / "runtime-launch-v1.json"
    receipt: dict[str, object] = {
        "format": "workbench-runtime-launch-receipt-v1",
        "schema_version": 1,
        "launch_id": "sha256:" + ("1" * 64),
        "outcome": "checkpoint-reached",
        "project": {"name": "Supersymmetry", "version": "test"},
        "materialization_id": "sha256:" + ("2" * 64),
        "launcher": {
            "instance_id": "workbench-supersymmetry-test",
            "projection_uri": projection.as_uri(),
            "host": {"os": "windows"},
            "process_state": "running",
        },
        "projection": {"projection_uri": projection.as_uri()},
        "launch_policy": {"checkpoint": "fml-client-loaded"},
        "observation": {"checkpoint": {
            "id": "fml-client-loaded",
            "marker": "Forge Mod Loader has successfully loaded 210 mods",
            "source": "minecraft-latest-log",
        }},
        "evidence": [],
        "limitations": [],
        "target": {
            "run_root_uri": run_root.as_uri(),
            "receipt_uri": receipt_path.as_uri(),
        },
    }
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "format": "workbench-runtime-launch-result-v1",
        "schema_version": 1,
        "outcome": "checkpoint-reached",
        "receipt": receipt,
    }, projection, run_root


def _closure_result(
    root: Path,
    *,
    outcome: str = "completed",
    lifecycle_state: str = "exited",
    checks: list[dict[str, object]] | None = None,
    failures: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    instance_id = "workbench-supersymmetry-closure-test"
    attached = lifecycle_state not in {
        "attach-timeout",
        "launch-ended-before-attach",
    }
    lifecycle = {
        "state": lifecycle_state,
        "observed_pids": [42] if attached else [],
        "attached_at": "2026-08-16T00:00:00.000Z" if attached else None,
    }
    return {
        "outcome": outcome,
        "receipt": {
            "outcome": outcome,
            "launch": {
                "instance_id": instance_id,
                "process_observation": lifecycle,
            },
            "analysis_lifecycle_checks": (
                [{"state": "absent", "observed_pids": []}]
                if checks is None
                else checks
            ),
            "analysis_failures": [] if failures is None else failures,
        },
        "launch_receipt": {
            "outcome": "checkpoint-reached",
            "launcher": {
                "executable_uri": (root / "PrismLauncher.exe").as_uri(),
                "host": {"os": "windows"},
                "instance_id": instance_id,
                "process_state": "running",
            },
            "launch_policy": {
                "observation_boundary": "projected-client-process-exit"
            },
            "observation": {"session_exit": lifecycle},
        },
    }


class RuntimeObserveTest(unittest.TestCase):
    def test_configuration_and_path_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(
            RuntimeObserveError,
            "mutually exclusive",
        ):
            observe_project_runtime(
                REPOSITORY_ROOT,
                REPOSITORY_ROOT,
                launcher_executable=REPOSITORY_ROOT / "launcher",
                launcher_root=REPOSITORY_ROOT / "launcher-root",
                configuration=object(),  # type: ignore[arg-type]
                config_path="workbench.toml",
            )

    def test_invalid_bounds_fail_before_launch(self) -> None:
        cases = (
            {"timeout_seconds": 0.0},
            {"attach_timeout": float("inf")},
            {"session_timeout": float("nan")},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with patch(
                    "workbench_shell.runtime_observe.launch_project_runtime",
                ) as launch:
                    with self.assertRaisesRegex(
                        RuntimeObserveError,
                        "must be finite",
                    ):
                        observe_project_runtime(
                            REPOSITORY_ROOT,
                            REPOSITORY_ROOT,
                            launcher_executable=REPOSITORY_ROOT / "launcher",
                            launcher_root=REPOSITORY_ROOT / "launcher-root",
                            _process_probe=lambda _instance: frozenset(),
                            **overrides,
                        )
                launch.assert_not_called()

    def test_unsupported_host_fails_before_launch(self) -> None:
        with (
            patch(
                "workbench_shell.runtime_observe._probe_launcher",
                return_value=(
                    REPOSITORY_ROOT / "launcher",
                    {},
                    {
                        "os": "linux",
                        "architecture": "x64",
                        "system": "Linux",
                        "machine": "x86_64",
                    },
                ),
            ),
            patch(
                "workbench_shell.runtime_observe.launch_project_runtime",
            ) as launch,
        ):
            with self.assertRaisesRegex(
                RuntimeObserveError,
                "requires a Windows launcher host",
            ):
                observe_project_runtime(
                    REPOSITORY_ROOT,
                    REPOSITORY_ROOT,
                    launcher_executable=REPOSITORY_ROOT / "launcher",
                    launcher_root=REPOSITORY_ROOT / "launcher-root",
                )
        launch.assert_not_called()

    def test_launcher_preflight_is_read_only_and_exercises_process_authority(
        self,
    ) -> None:
        launcher = REPOSITORY_ROOT / "PrismLauncher.exe"
        launcher_root = REPOSITORY_ROOT / "launcher-root"
        host = {
            "os": "windows",
            "architecture": "x64",
            "system": "Windows",
            "machine": "AMD64",
        }
        with (
            patch(
                "workbench_shell.runtime_observe._probe_launcher",
                return_value=(launcher, {"family": "prism"}, host),
            ),
            patch(
                "workbench_shell.runtime_observe._probe_launcher_root",
                return_value={},
            ) as root_probe,
            patch(
                "workbench_shell.runtime_observe._windows_runtime_pids",
                return_value=frozenset(),
            ) as process_probe,
        ):
            readiness = preflight_close_observer_launcher(
                launcher,
                launcher_root,
                "prism",
            )

        self.assertEqual(host, readiness["host"])
        root_probe.assert_called_once_with(launcher_root.resolve(), "prism")
        process_probe.assert_called_once_with(
            "workbench-runtime-readiness-probe",
            "PrismLauncher.exe",
        )

    def test_terminal_gate_requires_closed_receipt_and_two_empty_inventories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls: list[tuple[str, str]] = []

            def absent(instance_id: str, image_name: str) -> frozenset[int]:
                calls.append((instance_id, image_name))
                return frozenset()

            require_runtime_processes_closed(
                _closure_result(
                    root,
                    outcome="analysis-incomplete",
                    failures=[{"kind": "runtime-worldgen-audit-failed"}],
                ),
                _owned_process_probe=absent,
            )

        self.assertEqual(2, len(calls))
        self.assertEqual(
            ["PrismLauncher.exe", "PrismLauncher.exe"],
            [image_name for _instance_id, image_name in calls],
        )

    def test_native_inventory_binds_launcher_and_java_to_instance_token(
        self,
    ) -> None:
        with patch(
            "workbench_shell.runtime_observe.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="41\n42\n"),
        ) as query:
            pids = _windows_runtime_pids(
                "workbench-supersymmetry-closure-test",
                "PrismLauncher.exe",
            )

        self.assertEqual(frozenset({41, 42}), pids)
        command = query.call_args.args[0]
        script = command[-1]
        self.assertIn("PrismLauncher.exe", script)
        self.assertIn("javaw.exe", script)
        self.assertIn("java.exe", script)
        self.assertIn("workbench-supersymmetry-closure-test", script)

    def test_terminal_gate_rejects_ambiguous_or_reappearing_processes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                (
                    "session-timeout",
                    _closure_result(
                        root,
                        outcome="timed-out",
                        lifecycle_state="session-timeout",
                    ),
                ),
                (
                    "attach-timeout",
                    _closure_result(
                        root,
                        outcome="timed-out",
                        lifecycle_state="attach-timeout",
                    ),
                ),
                (
                    "probe-failed",
                    _closure_result(
                        root,
                        outcome="probe-failed",
                        lifecycle_state="probe-failed",
                    ),
                ),
                (
                    "post-exit-reappearance",
                    _closure_result(
                        root,
                        outcome="analysis-incomplete",
                        checks=[
                            {
                                "state": "process-reappeared",
                                "observed_pids": [99],
                            }
                        ],
                        failures=[
                            {"kind": "live-root-lifecycle-invalid"}
                        ],
                    ),
                ),
            )
            for label, result in cases:
                with self.subTest(label=label):
                    with self.assertRaisesRegex(
                        RuntimeObserveError,
                        "does not prove a closed",
                    ):
                        require_runtime_processes_closed(
                            result,
                            _owned_process_probe=(
                                lambda _instance, _image: frozenset()
                            ),
                        )

            samples = iter((frozenset(), frozenset({99})))
            with self.assertRaisesRegex(
                RuntimeObserveError,
                "remains live",
            ):
                require_runtime_processes_closed(
                    _closure_result(root),
                    _owned_process_probe=(
                        lambda _instance, _image: next(samples)
                    ),
                )

    def test_capture_rejects_symlinked_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / ".minecraft"
            outside = root / "outside"
            destination = root / "capture"
            runtime.mkdir()
            outside.mkdir()
            (outside / "latest.log").write_text("external\n", encoding="utf-8")
            (runtime / "logs").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(
                RuntimeObserveError,
                "contains a symbolic link",
            ):
                _capture_final_evidence(
                    runtime,
                    destination,
                    redact_values=(),
                )

    def test_requires_seen_process_before_accepting_disappearance(self) -> None:
        clock = _Clock()

        result = _observe_process_exit(
            "workbench-test",
            attach_timeout=2,
            session_timeout=10,
            process_probe=lambda _instance: frozenset(),
            clock=clock.now,
            sleep=clock.sleep,
            poll_interval=1,
        )

        self.assertEqual(result["state"], "attach-timeout")
        self.assertEqual(result["observed_pids"], [])

    def test_captures_final_logs_and_runs_both_analyzers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            state_root = root / "external-runtime-state"
            launch, projection, run_root = _launch_fixture(
                root,
                state_root=state_root,
            )
            sequence = iter((
                frozenset({42}),
                frozenset({42}),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
            ))
            clock = _Clock()
            diagnosis = {
                "format": "workbench-runtime-diagnosis-v2",
                "schema_version": 2,
                "diagnosis_id": "sha256:" + ("3" * 64),
                "state": "observed",
            }
            audit = {
                "format": "workbench-runtime-worldgen-audit-v1",
                "schema_version": 1,
                "audit_id": "sha256:" + ("4" * 64),
                "state": "findings-observed",
                "findings": [],
            }
            anvil_observation = {
                "format": "atlas-experimental-anvil-region-observation-v1",
                "schema_version": 1,
                "observation_id": "sha256:" + ("5" * 64),
                "state": "no-findings-observed",
                "facts": {
                    "summary": {
                        "region_file_count": 1,
                        "allocated_chunk_count": 2,
                    },
                },
            }
            configuration = object()
            with (
                patch(
                    "workbench_shell.runtime_observe."
                    "load_workbench_configuration",
                    return_value=configuration,
                ) as load_configuration,
                patch(
                    "workbench_shell.runtime_observe.launch_project_runtime",
                    return_value=launch,
                ) as launch_runtime,
                patch(
                    "workbench_shell.runtime_observe.diagnose_project_runtime",
                    return_value=diagnosis,
                ) as diagnose,
                patch(
                    "workbench_shell.runtime_observe.audit_project_worldgen",
                    return_value=audit,
                ) as worldgen,
                patch(
                    "workbench_shell.runtime_observe.iter_runtime_anvil_worlds",
                    return_value=iter([{
                        "world_name": "Observed World",
                        "world_uri": (projection / ".minecraft/saves/world").as_uri(),
                        "observation": anvil_observation,
                    }]),
                ) as anvil,
            ):
                result = observe_project_runtime(
                    root,
                    workspace,
                    state_root=state_root,
                    launcher_executable=root / "launcher.exe",
                    launcher_root=root / "launcher",
                    _process_probe=lambda _instance: next(sequence),
                    _clock=clock.now,
                    _sleep=clock.sleep,
                    _poll_interval=1,
                )

            self.assertEqual(result["outcome"], "completed")
            load_configuration.assert_called_once_with(
                root.resolve(),
                Path("workbench.toml"),
            )
            self.assertIs(
                launch_runtime.call_args.kwargs["configuration"],
                configuration,
            )
            self.assertEqual(
                launch_runtime.call_args.kwargs["state_root"],
                state_root.resolve(),
            )
            self.assertFalse((root / ".workbench").exists())
            self.assertEqual(
                result["receipt"]["launch"]["process_observation"]["state"],
                "exited",
            )
            final_log = run_root / "final/minecraft-latest.log"
            self.assertTrue(final_log.is_file())
            self.assertEqual(
                sha256(final_log.read_bytes()).hexdigest(),
                next(
                    item["sha256"]
                    for item in result["receipt"]["evidence"]
                    if item["label"] == "minecraft-latest-log"
                ),
            )
            final_receipt = run_root / "runtime-launch-v3.json"
            self.assertEqual(
                json.loads(final_receipt.read_text(encoding="utf-8"))["format"],
                "workbench-runtime-launch-receipt-v3",
            )
            diagnose.assert_called_once_with(
                root.resolve(),
                workspace.resolve(),
                launch_receipt=final_receipt,
                configuration=configuration,
            )
            worldgen.assert_called_once_with(
                root.resolve(),
                workspace.resolve(),
                runtime_root=projection / ".minecraft",
                configuration=configuration,
            )
            anvil.assert_called_once_with(
                root.resolve(),
                projection / ".minecraft",
            )
            self.assertEqual(
                result["receipt"]["anvil_observations"][0]["observation_id"],
                anvil_observation["observation_id"],
            )
            self.assertTrue(all(
                item["state"] == "absent"
                for item in result["receipt"]["analysis_lifecycle_checks"]
            ))
            self.assertTrue(
                (run_root / "atlas-anvil-observation-000-v1.json").is_file()
            )
            self.assertTrue((run_root / "runtime-observation-v1.json").is_file())

    def test_timeout_retains_non_quiescent_snapshot_and_skips_worldgen(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            launch, _projection, run_root = _launch_fixture(root)
            clock = _Clock()
            diagnosis = {
                "format": "workbench-runtime-diagnosis-v2",
                "schema_version": 2,
                "diagnosis_id": "sha256:" + ("3" * 64),
                "state": "observed",
            }
            with (
                patch(
                    "workbench_shell.runtime_observe.launch_project_runtime",
                    return_value=launch,
                ),
                patch(
                    "workbench_shell.runtime_observe.diagnose_project_runtime",
                    return_value=diagnosis,
                ),
                patch(
                    "workbench_shell.runtime_observe.audit_project_worldgen",
                ) as worldgen,
                patch(
                    "workbench_shell.runtime_observe.iter_runtime_anvil_worlds",
                ) as anvil,
            ):
                result = observe_project_runtime(
                    root,
                    workspace,
                    launcher_executable=root / "launcher.exe",
                    launcher_root=root / "launcher",
                    session_timeout=2,
                    _process_probe=lambda _instance: frozenset({42}),
                    _clock=clock.now,
                    _sleep=clock.sleep,
                    _poll_interval=1,
                    configuration=object(),  # type: ignore[arg-type]
                )

            self.assertEqual(result["outcome"], "timed-out")
            self.assertIsNone(result["worldgen_audit"])
            self.assertEqual(
                result["launch_receipt"]["launch_policy"][
                    "observation_boundary"
                ],
                "incomplete-process-observation",
            )
            self.assertTrue(
                (run_root / "incomplete-snapshot/minecraft-latest.log").is_file()
            )
            self.assertFalse((run_root / "final").exists())
            self.assertEqual(
                result["receipt"]["worldgen_audit"]["state"],
                "not-run",
            )
            worldgen.assert_not_called()
            anvil.assert_not_called()

    def test_anvil_failure_retains_other_analysis_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            launch, _projection, run_root = _launch_fixture(root)
            sequence = iter((
                frozenset({42}),
                frozenset({42}),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
                frozenset(),
            ))
            clock = _Clock()
            diagnosis = {
                "format": "workbench-runtime-diagnosis-v2",
                "schema_version": 2,
                "diagnosis_id": "sha256:" + ("3" * 64),
                "state": "checkpoint-reached",
            }
            audit = {
                "format": "workbench-runtime-worldgen-audit-v1",
                "schema_version": 1,
                "audit_id": "sha256:" + ("4" * 64),
                "state": "findings-observed",
                "findings": [],
            }
            with (
                patch(
                    "workbench_shell.runtime_observe.launch_project_runtime",
                    return_value=launch,
                ),
                patch(
                    "workbench_shell.runtime_observe.diagnose_project_runtime",
                    return_value=diagnosis,
                ),
                patch(
                    "workbench_shell.runtime_observe.audit_project_worldgen",
                    return_value=audit,
                ),
                patch(
                    "workbench_shell.runtime_observe.iter_runtime_anvil_worlds",
                    side_effect=RuntimeAnvilObserveError("aggregate limit"),
                ),
            ):
                result = observe_project_runtime(
                    root,
                    workspace,
                    launcher_executable=root / "launcher.exe",
                    launcher_root=root / "launcher",
                    _process_probe=lambda _instance: next(sequence),
                    _clock=clock.now,
                    _sleep=clock.sleep,
                    _poll_interval=1,
                    configuration=object(),  # type: ignore[arg-type]
                )

            self.assertEqual(result["outcome"], "analysis-incomplete")
            self.assertTrue((run_root / "runtime-diagnosis-v2.json").is_file())
            self.assertTrue(
                (run_root / "runtime-worldgen-audit-v1.json").is_file()
            )
            self.assertTrue((run_root / "runtime-observation-v1.json").is_file())
            self.assertEqual(
                result["receipt"]["anvil_observations"][0]["state"],
                "failed",
            )
            self.assertEqual(
                result["receipt"]["diagnosis"]["diagnosis_id"],
                diagnosis["diagnosis_id"],
            )

    def test_reappearing_process_blocks_live_root_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            launch, _projection, run_root = _launch_fixture(root)
            sequence = iter((
                frozenset({42}),
                frozenset({42}),
                frozenset(),
                frozenset(),
                frozenset({99}),
            ))
            clock = _Clock()
            diagnosis = {
                "format": "workbench-runtime-diagnosis-v2",
                "schema_version": 2,
                "diagnosis_id": "sha256:" + ("3" * 64),
                "state": "checkpoint-reached",
            }
            with (
                patch(
                    "workbench_shell.runtime_observe.launch_project_runtime",
                    return_value=launch,
                ),
                patch(
                    "workbench_shell.runtime_observe.diagnose_project_runtime",
                    return_value=diagnosis,
                ),
                patch(
                    "workbench_shell.runtime_observe.audit_project_worldgen",
                ) as worldgen,
                patch(
                    "workbench_shell.runtime_observe.iter_runtime_anvil_worlds",
                ) as anvil,
            ):
                result = observe_project_runtime(
                    root,
                    workspace,
                    launcher_executable=root / "launcher.exe",
                    launcher_root=root / "launcher",
                    _process_probe=lambda _instance: next(sequence),
                    _clock=clock.now,
                    _sleep=clock.sleep,
                    _poll_interval=1,
                    configuration=object(),  # type: ignore[arg-type]
                )

            self.assertEqual(result["outcome"], "analysis-incomplete")
            self.assertTrue((run_root / "runtime-diagnosis-v2.json").is_file())
            self.assertEqual(
                result["receipt"]["analysis_lifecycle_checks"][0]["state"],
                "process-reappeared",
            )
            self.assertEqual(
                result["receipt"]["worldgen_audit"]["state"],
                "not-run",
            )
            worldgen.assert_not_called()
            anvil.assert_not_called()


if __name__ == "__main__":
    unittest.main()
