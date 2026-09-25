"""Module-day logs, role paths, and concurrent command attribution."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from workbench_core.environment_resolution import resolve_environment
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.output_routing import OutputInvocation
from workbench_core.user_preferences import set_location


def _module() -> InstalledModule:
    capability = Capability("sample.run", ("sample",), "sample_plugin:run", "run")
    return InstalledModule(
        "sample", "workbench-sample", "0.1.0", "available",
        module=Module("sample", "0.1.0", (capability,)),
    )


def _events(log_root: Path, module_id: str = "sample") -> list[dict]:
    module_directory = log_root / "modules" / module_id
    paths = sorted(module_directory.glob("*.jsonl"))
    if not paths:
        raise AssertionError("expected a module-day log")
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src/workbench_core/schemas/workbench-module-log-event-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    events = [json.loads(line) for path in paths for line in path.read_text(encoding="utf-8").splitlines()]
    for event in events:
        validator.validate(event)
    return events


class OutputRoutingTests(unittest.TestCase):
    def test_saved_location_controls_module_day_log_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            set_location("logs", "~/team-logs", environment=environment)
            set_location("artifacts", "~/team-artifacts", environment=environment)
            resolved = resolve_environment(home, environment=environment)
            context = ExecutionContext(
                resolved.workspace, resolved.state_root, locations=resolved.locations
            )

            def run(argv, *, context):
                context.output_path("artifacts", "result.txt").write_text(
                    "result\n", encoding="utf-8"
                )
                print("done")
                return 0

            with (
                patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, dispatch(["sample"], context, (_module(),)))
            events = _events(home / "team-logs")
            self.assertEqual(["started", "python_text", "finished"], [event["kind"] for event in events])
            finished = events[-1]
            output = finished["outputs"][0]
            self.assertEqual("completed", finished["outcome"])
            self.assertEqual("result\n", Path(output["path"]).read_text(encoding="utf-8"))
            self.assertEqual(home / "team-artifacts" / "outputs" / "artifacts" / "sample", Path(output["path"]).parent)
            self.assertEqual(["sample"], [path.name for path in (home / "team-logs/modules").iterdir()])
            self.assertFalse(any(path.is_dir() for path in (home / "team-logs/modules/sample").iterdir()))

    def test_configured_root_collects_python_text_and_role_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            log_root = home / "chosen-logs"
            artifact_root = home / "chosen-artifacts"
            context = ExecutionContext(
                home, home / "state",
                locations={"logs": log_root, "artifacts": artifact_root},
            )

            def run(argv, *, context):
                self.assertEqual(["argument"], argv)
                print("visible stdout")
                print("visible stderr", file=sys.stderr)
                context.output_path("artifacts", "result.json").write_text(
                    '{"ok": true}\n', encoding="utf-8"
                )
                context.output_path("logs", "owner.log").write_text("owner log\n", encoding="utf-8")
                return 0

            terminal_out = io.StringIO()
            terminal_err = io.StringIO()
            with (
                patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}),
                redirect_stdout(terminal_out),
                redirect_stderr(terminal_err),
            ):
                self.assertEqual(0, dispatch(["sample", "argument"], context, (_module(),)))
            self.assertEqual("visible stdout\n", terminal_out.getvalue())
            self.assertEqual("visible stderr\n", terminal_err.getvalue())
            events = _events(log_root)
            self.assertEqual("visible stdout\n", "".join(
                event["text"] for event in events if event["kind"] == "python_text" and event["stream"] == "stdout"
            ))
            self.assertEqual("visible stderr\n", "".join(
                event["text"] for event in events if event["kind"] == "python_text" and event["stream"] == "stderr"
            ))
            finished = events[-1]
            self.assertEqual("completed", finished["outcome"])
            self.assertEqual(0, finished["exit_code"])
            outputs = {row["role"]: row for row in finished["outputs"]}
            self.assertEqual(artifact_root / "outputs/artifacts/sample", Path(outputs["artifacts"]["path"]).parent)
            self.assertEqual('{"ok": true}\n', Path(outputs["artifacts"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(log_root / "outputs/logs/sample", Path(outputs["logs"]["path"]).parent)
            self.assertEqual("owner log\n", Path(outputs["logs"]["path"]).read_text(encoding="utf-8"))

    def test_failure_and_missing_output_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            logs = home / "logs"
            context = ExecutionContext(home, home / "state", locations={"logs": logs})

            def fails(argv, *, context):
                print("started")
                with self.assertRaisesRegex(ModuleError, "portable filename"):
                    context.output_path("logs", "../escape.log")
                raise RuntimeError("owner failed")

            with (
                patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=fails)}),
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(ModuleError, "owner failed"):
                    dispatch(["sample"], context, (_module(),))
            finished = _events(logs)[-1]
            self.assertEqual("failed", finished["outcome"])
            self.assertEqual("ModuleError", finished["error_type"])
            self.assertFalse((home / "escape.log").exists())

            def missing(argv, *, context):
                context.output_path("logs", "never-written.txt")
                return 0

            with patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=missing)}):
                with self.assertRaisesRegex(ModuleError, "requested output was not written"):
                    dispatch(["sample"], context, (_module(),))
            finished = _events(logs)[-1]
            self.assertEqual("failed", finished["outcome"])
            self.assertIsNone(finished["outputs"][0]["file"])

    def test_shared_root_keeps_roles_in_distinct_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with OutputInvocation({"logs": root, "artifacts": root}, "sample", "sample.run") as run:
                log_path = run.output_path("logs", "same.txt")
                artifact_path = run.output_path("artifacts", "same.txt")
                log_path.write_text("log\n", encoding="utf-8")
                artifact_path.write_text("artifact\n", encoding="utf-8")
                run.exit_code = 0
            self.assertNotEqual(log_path, artifact_path)
            self.assertEqual("completed", _events(root)[-1]["outcome"])

    def test_concurrent_runs_keep_text_with_its_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_entered, second_entered, first_exited = Event(), Event(), Event()
            failures: list[BaseException] = []
            run_ids: dict[str, str] = {}

            def first():
                try:
                    with OutputInvocation({"logs": root}, "sample", "sample.first") as run:
                        run_ids["first"] = run.run_id
                        first_entered.set()
                        self.assertTrue(second_entered.wait(5))
                        print("FIRST")
                        run.exit_code = 0
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    first_exited.set()

            def second():
                try:
                    self.assertTrue(first_entered.wait(5))
                    with OutputInvocation({"logs": root}, "sample", "sample.second") as run:
                        run_ids["second"] = run.run_id
                        second_entered.set()
                        self.assertTrue(first_exited.wait(5))
                        print("SECOND")
                        run.exit_code = 0
                except BaseException as exc:
                    failures.append(exc)

            terminal = io.StringIO()
            with redirect_stdout(terminal):
                threads = [Thread(target=first), Thread(target=second)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertIs(sys.stdout, terminal)
            self.assertEqual([], failures)
            events = _events(root)
            text_by_run = {
                run_id: "".join(event["text"] for event in events
                                if event["run_id"] == run_id and event["kind"] == "python_text")
                for run_id in run_ids.values()
            }
            self.assertEqual("FIRST\n", text_by_run[run_ids["first"]])
            self.assertEqual("SECOND\n", text_by_run[run_ids["second"]])
            self.assertEqual(2, sum(event["kind"] == "finished" for event in events))

    def test_parallel_processes_append_complete_module_day_events(self) -> None:
        worker = '''from pathlib import Path
import sys
from workbench_core.output_routing import OutputInvocation
with OutputInvocation({'logs': Path(sys.argv[1])}, 'sample', 'sample.concurrent') as run:
    for index in range(50):
        print(f'{sys.argv[2]}:{index}')
    run.exit_code = 0
'''
        suite_root = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(suite_root / "api/src"), str(suite_root / "core/src"),
        ))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", worker, str(root), str(index)],
                    env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                for index in range(4)
            ]
            try:
                results = [process.communicate(timeout=15) for process in processes]
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            self.assertEqual([0] * 4, [process.returncode for process in processes], results)
            events = _events(root)
            text_by_run: dict[str, str] = {}
            for event in events:
                if event["kind"] == "python_text":
                    text_by_run[event["run_id"]] = text_by_run.get(event["run_id"], "") + event["text"]
            self.assertEqual([50] * 4, sorted(len(text.splitlines()) for text in text_by_run.values()))
            self.assertEqual(4, sum(event["kind"] == "finished" for event in events))

    def test_symlinked_log_root_fails_before_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            outside = home / "outside"
            outside.mkdir()
            (home / "alias").symlink_to(outside, target_is_directory=True)
            context = ExecutionContext(home, home / "state", locations={"logs": home / "alias/logs"})
            handler = SimpleNamespace(run=lambda argv, *, context: self.fail("handler ran"))
            with patch.dict(sys.modules, {"sample_plugin": handler}):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    dispatch(["sample"], context, (_module(),))
            self.assertFalse((outside / "logs").exists())


if __name__ == "__main__":
    unittest.main()
