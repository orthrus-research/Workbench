"""Failure-first tests for the generic Cleanroom daily-loop composition."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/workbench-shell/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_core.development import enable_source_checkout  # noqa: E402
enable_source_checkout(ROOT)

from workbench_shell.cleanroom_dev_loop import (  # noqa: E402
    CleanroomDevLoopError,
    CleanroomDevLoopStageCustodyPorts,
    cleanroom_dev_loop_jar_only_runtime_argv_v2,
    execute_cleanroom_dev_loop,
    find_cleanroom_dev_loop_stage,
    load_cleanroom_dev_loop_receipt,
    materialize_cleanroom_dev_loop_workspace_pair_v2,
    plan_cleanroom_dev_loop,
    recover_cleanroom_dev_loop,
    stage_cleanroom_dev_loop_artifact_v2,
    stop_cleanroom_dev_loop_runtime_v2,
    validate_cleanroom_dev_loop_jar_only_classpath_v2,
    validate_cleanroom_dev_loop_plan,
)
from workbench_core.sessions import resolve_live_console_owner_reference  # noqa: E402


SCHEMA_ROOT = ROOT / "modules/workbench-shell/schemas"


def _validate_schema(name: str, value: dict) -> None:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _java(home: Path) -> None:
    _write(home / "release", 'JAVA_VERSION="25.0.1"\n')
    _write(home / "bin/java", "#!/bin/sh\nexit 0\n", executable=True)


def _fake_gradle(path: Path) -> None:
    script = r'''#!/usr/bin/env python3
import json
from pathlib import Path
import signal
import sys
import time
from zipfile import ZipFile

arguments = sys.argv[1:]
project = Path(arguments[arguments.index("-p") + 1])
if (Path(__file__).parent / "fail-build").exists() and "clean" in arguments:
    print("deliberate compile failure", file=sys.stderr, flush=True)
    raise SystemExit(42)
if "clean" in arguments:
    artifact = project.parents[4] / ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop/libs/workbench-daily-loop-1.0.0.jar"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(artifact, "w") as jar:
        jar.writestr("mcmod.info", json.dumps([{"modid":"workbench_daily_loop","version":"1.0.0"}]))
        jar.writestr("pack.mcmeta", "{}")
        jar.writestr("mixins.workbench_daily_loop.json", "{}")
        jar.writestr("mixins.workbench_daily_loop.refmap.json", '{"mappings":{"a":"b"}}')
        jar.writestr("assets/workbench_daily_loop/lang/en_us.lang", "tile.workbench_daily_loop.probe_block.name=Probe Block\n")
        jar.writestr("dev/workbench/dailyloop/DailyLoopMod.class", bytes.fromhex("cafebabe00000045"))
        jar.writestr("dev/workbench/dailyloop/DailyLoopContent.class", bytes.fromhex("cafebabe00000045"))
        jar.writestr("dev/workbench/dailyloop/DailyLoopProbe.class", bytes.fromhex("cafebabe00000045"))
        jar.writestr("dev/workbench/dailyloop/mixin/MixinBlock.class", bytes.fromhex("cafebabe00000045"))
    print("fixture artifact built", flush=True)
    raise SystemExit(0)
if "runClient" in arguments:
    if not (Path(__file__).parent / "wrong-client-marker").exists():
        print("WORKBENCH_DAILY_LOOP_CLIENT_RESOURCE_READY key=tile.workbench_daily_loop.probe_block.name value=Probe Block", flush=True)
    else:
        print("client loaded without the fixture resource", flush=True)
        raise SystemExit(0)
elif "runServer" in arguments:
    target_argument = next(
        value for value in arguments
        if value.startswith("-PworkbenchDailyLoopRunDir=")
    )
    target = Path(target_argument.split("=", 1)[1])
    if not (target / "eula.txt").is_file() or (target / "eula.txt").read_bytes() != b"eula=true\n":
        print("server stopped before startup because its isolated EULA is absent", flush=True)
        raise SystemExit(0)
    print('Done (1.000s)! For help, type "help"', flush=True)
    print("WORKBENCH_DAILY_LOOP_COMMON_READY registry=workbench_daily_loop:probe_block fixture=1.0.0", flush=True)
else:
    raise SystemExit(9)
def raise_exit():
    raise SystemExit(0)
signal.signal(signal.SIGINT, lambda *_: raise_exit())
time.sleep(30)
'''
    _write(path, script, executable=True)


class _CustodyPorts(CleanroomDevLoopStageCustodyPorts):
    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events

    def stage_allocated(self, *, plan: dict, stage: str, owner_ref: dict) -> None:
        if plan["state"] != "ready" or owner_ref["last_verified_state"] != "allocated":
            raise AssertionError("prelaunch custody did not receive allocated owner truth")
        self.events.append((stage, "allocated"))

    def stage_terminal(
        self,
        *,
        plan: dict,
        stage: str,
        owner_ref: dict,
        stage_result: dict,
    ) -> None:
        if plan["state"] != "ready" or stage_result["state"] not in {"passed", "failed"}:
            raise AssertionError("terminal custody did not receive the stage decision")
        if owner_ref["last_verified_state"] not in {"complete", "failed", "cancelled"}:
            raise AssertionError("terminal custody owner is not terminal")
        self.events.append((stage, "terminal"))


class CleanroomDevLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.gradle = self.root / "tools/gradle"
        self.java = self.root / "tools/jdk-25"
        self.state = self.root / "state"
        _fake_gradle(self.gradle)
        _java(self.java)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def plan(self) -> dict:
        return plan_cleanroom_dev_loop(
            ROOT,
            gradle_cmd=self.gradle,
            java_home=self.java,
            state_root=self.state,
            sides=("client", "server"),
        )

    def test_read_only_plan_binds_fixture_toolchain_commands_and_separate_sides(self) -> None:
        before = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        plan = self.plan()
        _validate_schema("workbench-cleanroom-dev-loop-plan-v1.schema.json", plan)
        self.assertEqual(plan, validate_cleanroom_dev_loop_plan(plan))
        self.assertEqual("ready", plan["state"])
        self.assertEqual(["client", "server"], plan["request"]["sides"])
        self.assertEqual("sha256:" + sha256(self.gradle.read_bytes()).hexdigest(), plan["toolchain"]["gradle"]["sha256"])
        build_argv = plan["stages"]["build"]["argv"]
        projected_fixture = Path(build_argv[build_argv.index("-p") + 1])
        self.assertTrue(
            projected_fixture.is_relative_to(
                self.state / "source-projections/cleanroom"
            ),
            "the reviewed state root must own the projected fixture",
        )
        self.assertEqual(
            str(self.state / "gradle-home/generic-mod-daily-loop"),
            plan["toolchain"]["environment"]["GRADLE_USER_HOME"],
        )
        self.assertEqual("runClient", plan["stages"]["client"]["argv"][-1])
        self.assertEqual("runServer", plan["stages"]["server"]["argv"][-1])
        self.assertEqual(
            {
                "operation": "materialize-isolated-server-eula",
                "scope": "fresh-isolated-run-target",
                "target_uri": plan["stages"]["server"]["target_root_uri"]
                + "/eula.txt",
                "sha256": "sha256:ee27072e4a23e088522f740ddaab0c7c4145c186969e90a86254faa3a5ec5ce6",
                "size": 10,
            },
            plan["stages"]["server"]["preparation"],
        )
        for side in ("client", "server"):
            self.assertIn(
                "-PworkbenchDailyLoopRunDir="
                + plan["stages"][side]["target_root_uri"].removeprefix("file://"),
                plan["stages"][side]["argv"],
            )
        self.assertNotEqual(
            plan["stages"]["client"]["target_root_uri"],
            plan["stages"]["server"]["target_root_uri"],
        )
        after = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        self.assertEqual(before, after, "planning created retained or build state")

    def test_debug_fails_before_plan_instead_of_claiming_an_unproved_endpoint(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "JDWP attach"):
            plan_cleanroom_dev_loop(
                ROOT,
                gradle_cmd=self.gradle,
                java_home=self.java,
                state_root=self.state,
                debug=True,
            )
        self.assertFalse(self.state.exists())

    def test_jar_only_v2_stages_exact_bytes_and_rejects_dev_output_origin(self) -> None:
        plan = self.plan()
        pair = materialize_cleanroom_dev_loop_workspace_pair_v2(ROOT, plan)
        self.assertEqual(
            ROOT.resolve().as_uri(), pair["authority"]["installed_suite_uri"]
        )
        self.assertNotEqual(
            pair["baseline"]["source_sha256"],
            pair["candidate"]["source_sha256"],
        )
        self.assertEqual(
            "disposable-candidate-workspace-only",
            pair["candidate_edit"]["scope"],
        )
        result = execute_cleanroom_dev_loop(ROOT, plan)
        self.assertEqual("passed", result["outcome"])
        built = Path(result["receipt"]["artifact"]["uri"].removeprefix("file://"))
        staged = stage_cleanroom_dev_loop_artifact_v2(
            ROOT,
            state_root=self.state,
            built_artifact=built,
        )
        installed = Path(staged["installed_uri"].removeprefix("file://"))
        self.assertEqual(staged["built_sha256"], staged["installed_sha256"])

        dependency = self.root / "classpath/dependency.txt"
        _write(dependency, "not a fixture archive\n")
        validated = validate_cleanroom_dev_loop_jar_only_classpath_v2(
            [dependency],
            installed_artifact=installed,
            cleanroom_extra_path=[installed],
        )
        self.assertEqual("jar-only", validated["runtime_mode"])
        self.assertEqual(1, validated["fixture_origin_count"])
        self.assertEqual(0, validated["raw_classpath_fixture_origin_count"])
        self.assertEqual(1, validated["extra_path_fixture_origin_count"])

        development_output = self.root / "classpath/main-classes"
        _write(
            development_output
            / "dev/workbench/dailyloop/DailyLoopProbe.class",
            "development output must never reach the launch classpath",
        )
        with self.assertRaisesRegex(
            CleanroomDevLoopError, "raw-classpath fixture origin"
        ):
            validate_cleanroom_dev_loop_jar_only_classpath_v2(
                [dependency, development_output],
                installed_artifact=installed,
                cleanroom_extra_path=[installed],
            )

        agent = self.root / "agents/d01-code-source-agent.jar"
        _write(agent, "source-built agent placeholder for argv composition\n")
        capture = self.state / "d01-captures/client-code-source.txt"
        argv = cleanroom_dev_loop_jar_only_runtime_argv_v2(
            ROOT,
            plan,
            side="client",
            installed_artifact=installed,
            code_source_agent=agent,
            probe_capture=capture,
            debug_port=5005,
            workspace_root=Path(pair["baseline"]["workspace_uri"].removeprefix("file://")),
        )
        self.assertEqual("runClient", argv[-1])
        self.assertIn(
            f"-PworkbenchD01JarOnlyArtifact={installed}", argv
        )
        self.assertIn(
            f"-PworkbenchD01ProbeCapture={capture}", argv
        )
        self.assertIn("-PworkbenchD01DebugPort=5005", argv)

    def test_owned_stop_v2_targets_only_exact_live_runtime_group(self) -> None:
        program = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(child.pid, flush=True); time.sleep(60)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            child_pid = int(process.stdout.readline().strip())
            pgid = os.getpgid(process.pid)
            self.assertEqual(process.pid, pgid)
            self.assertEqual(pgid, os.getpgid(child_pid))
            result = stop_cleanroom_dev_loop_runtime_v2(
                frontend_pid=process.pid,
                frontend_pgid=pgid,
                timeout_seconds=5,
            )
            process.wait(timeout=10)
            self.assertTrue(result["stopped_through_workbench"])
            self.assertIn(process.pid, result["process_inventory_before_stop"])
            self.assertIn(child_pid, result["process_inventory_before_stop"])
            self.assertEqual([], result["process_inventory_after_stop"])
            self.assertEqual(0, result["remaining_descendant_count"])
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_build_failure_retains_failed_stage_and_never_starts_a_runtime(self) -> None:
        (self.gradle.parent / "fail-build").touch()
        result = execute_cleanroom_dev_loop(ROOT, self.plan())

        self.assertEqual("failed", result["outcome"])
        receipt = result["receipt"]
        _validate_schema(
            "workbench-cleanroom-dev-loop-receipt-v1.schema.json", receipt
        )
        self.assertEqual("failed", receipt["stages"]["build"]["state"])
        self.assertEqual("not-run", receipt["stages"]["client"]["state"])
        self.assertEqual("not-run", receipt["stages"]["server"]["state"])
        self.assertTrue(Path(result["receipt_path"]).is_file())
        owner = receipt["stages"]["build"]["owner_ref"]
        self.assertEqual("failed", owner["last_verified_state"])
        reopened = resolve_live_console_owner_reference(self.state, owner)
        for field in (
            "owner_id", "record_id", "record_kind", "uri", "digest",
            "last_verified_state",
        ):
            self.assertEqual(owner[field], reopened[field])

    def test_stage_custody_precedes_popen_and_observes_every_terminal_owner(self) -> None:
        events: list[tuple[str, str]] = []
        ports = _CustodyPorts(events)
        real_popen = subprocess.Popen

        def observed_popen(*args, **kwargs):
            events.append(("process", "popen"))
            return real_popen(*args, **kwargs)

        with patch("workbench_core.runner.subprocess.Popen", side_effect=observed_popen):
            result = execute_cleanroom_dev_loop(
                ROOT,
                self.plan(),
                stage_custody_ports=ports,
            )

        self.assertEqual("passed", result["outcome"])
        self.assertEqual(
            [
                ("build", "allocated"), ("process", "popen"), ("build", "terminal"),
                ("client", "allocated"), ("process", "popen"), ("client", "terminal"),
                ("server", "allocated"), ("process", "popen"), ("server", "terminal"),
            ],
            events,
        )

    def test_exact_artifact_runs_both_sides_to_markers_and_owned_stop(self) -> None:
        result = execute_cleanroom_dev_loop(ROOT, self.plan())

        self.assertEqual("passed", result["outcome"])
        receipt = result["receipt"]
        _validate_schema(
            "workbench-cleanroom-dev-loop-receipt-v1.schema.json", receipt
        )
        self.assertRegex(receipt["artifact"]["sha256"], r"^sha256:[0-9a-f]{64}$")
        for side in ("client", "server"):
            row = receipt["stages"][side]
            self.assertEqual("passed", row["state"])
            self.assertTrue(row["controlled_stop"])
            self.assertTrue(row["cleanup"]["contained"])
            self.assertEqual(row["required_markers"], row["observed_markers"])
            self.assertTrue(Path(row["console_session_uri"].removeprefix("file://")).is_dir())
        server_preparation = receipt["stages"]["server"]["preparation"]
        self.assertEqual("materialized", server_preparation["state"])
        eula = Path(server_preparation["target_uri"].removeprefix("file://"))
        self.assertEqual(b"eula=true\n", eula.read_bytes())
        self.assertTrue(eula.is_relative_to(Path(receipt["target"]["run_root_uri"].removeprefix("file://"))))
        recovered = recover_cleanroom_dev_loop(result["receipt_path"])
        _validate_schema(
            "workbench-cleanroom-dev-loop-recovery-v1.schema.json", recovered
        )
        self.assertEqual("clean", recovered["state"])
        self.assertIsNone(recovered["next_action"])

    def test_owner_lookup_loads_one_exact_receipt_and_rejects_ambiguity(self) -> None:
        (self.gradle.parent / "fail-build").touch()
        result = execute_cleanroom_dev_loop(ROOT, self.plan())
        receipt = result["receipt"]
        owner_record_id = receipt["stages"]["build"]["owner_ref"]["record_id"]

        self.assertEqual(receipt, load_cleanroom_dev_loop_receipt(result["receipt_path"]))
        found = find_cleanroom_dev_loop_stage(self.state, owner_record_id)
        self.assertEqual(receipt, found["receipt"])
        self.assertEqual("build", found["stage"])
        self.assertEqual(receipt["stages"]["build"], found["stage_result"])

        duplicate_root = self.state / "dev-loop" / ("f" * 64)
        duplicate_root.mkdir(mode=0o700)
        duplicate = json.loads(json.dumps(receipt))
        duplicate["target"] = {
            "run_root_uri": duplicate_root.as_uri(),
            "receipt_uri": (duplicate_root / "receipt.json").as_uri(),
        }
        duplicate.pop("receipt_id")
        duplicate["receipt_id"] = (
            "workbench-cleanroom-dev-loop-receipt:sha256:"
            + sha256(
                json.dumps(
                    duplicate,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        )
        _write(
            duplicate_root / "receipt.json",
            json.dumps(duplicate, indent=2, sort_keys=True) + "\n",
        )
        with self.assertRaisesRegex(CleanroomDevLoopError, "ambiguous"):
            find_cleanroom_dev_loop_stage(self.state, owner_record_id)

    def test_relative_fixture_tool_path_is_normalized_to_dev_loop_error(self) -> None:
        with self.assertRaisesRegex(
            CleanroomDevLoopError,
            "fixture owner input inspection failed",
        ):
            plan_cleanroom_dev_loop(
                ROOT,
                gradle_cmd=Path("relative-gradle"),
                java_home=self.java,
                state_root=self.state,
            )

        plan = self.plan()
        self.gradle.unlink()
        with self.assertRaisesRegex(
            CleanroomDevLoopError,
            "fixture owner input inspection failed",
        ):
            execute_cleanroom_dev_loop(ROOT, plan)

    def test_missing_client_marker_is_a_retained_failure_not_exit_zero_success(self) -> None:
        (self.gradle.parent / "wrong-client-marker").touch()
        result = execute_cleanroom_dev_loop(ROOT, self.plan())

        self.assertEqual("failed", result["outcome"])
        self.assertEqual("failed", result["receipt"]["stages"]["client"]["state"])
        self.assertEqual("not-run", result["receipt"]["stages"]["server"]["state"])
        recovered = recover_cleanroom_dev_loop(result["receipt_path"])
        _validate_schema(
            "workbench-cleanroom-dev-loop-recovery-v1.schema.json", recovered
        )
        self.assertEqual("retry-safe", recovered["state"])
        self.assertEqual(["client", "server"], recovered["remaining_sides"])
        self.assertEqual("dev.fixture-run", recovered["next_action"]["action_id"])
        self.assertEqual(
            "creates-fresh-isolated-target",
            recovered["next_action"]["mutation_posture"],
        )
        self.assertEqual(
            recovered["remaining_sides"],
            recovered["next_action"]["reconstruction_inputs"]["sides"],
        )
        self.assertEqual(
            Path(result["receipt"]["target"]["plan_uri"].removeprefix("file://")),
            Path(recovered["next_action"]["source_plan_uri"].removeprefix("file://")),
        )
        retry_inputs = recovered["next_action"]["reconstruction_inputs"]
        (self.gradle.parent / "wrong-client-marker").unlink()
        retry_plan = plan_cleanroom_dev_loop(
            ROOT,
            gradle_cmd=retry_inputs["gradle_cmd"],
            java_home=retry_inputs["java_home"],
            state_root=retry_inputs["state_root"],
            sides=tuple(retry_inputs["sides"]),
            debug=retry_inputs["debug"],
        )
        retry = execute_cleanroom_dev_loop(ROOT, retry_plan)
        self.assertEqual("passed", retry["outcome"])


if __name__ == "__main__":
    unittest.main()
