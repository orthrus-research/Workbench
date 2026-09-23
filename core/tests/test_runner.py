from __future__ import annotations

import base64
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

from jsonschema import Draft202012Validator

from workbench_core.render import EventFilter, JsonlRenderer, Renderer
from workbench_core.runner import RunnerError, ingest_files, supervise_process
from workbench_core.sessions import (
    EphemeralSession,
    RetainedSession,
    iter_events,
    live_console_owner_reference,
    read_live_console_owner_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]
EVENT_SCHEMA = json.loads(
    (ROOT / "modules/workbench-shell/schemas/workbench-live-console-event-v1.schema.json").read_text()
)
SESSION_SCHEMA = json.loads(
    (ROOT / "modules/workbench-shell/schemas/workbench-live-console-session-v1.schema.json").read_text()
)


class CollectRenderer(Renderer):
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.context = None
        self.summary = None
        self.closed = False

    def start(self, context):
        self.context = dict(context)

    def consume(self, event):
        self.events.append(dict(event))

    def finish(self, summary):
        self.summary = dict(summary)

    def close(self):
        self.closed = True


class CancelRenderer(CollectRenderer):
    def consume(self, event):
        super().consume(event)
        if event.get("message") == "[workbench] stage child-process started":
            self.cancellation_requested = True


class DescendantCancelRenderer(CollectRenderer):
    def consume(self, event):
        super().consume(event)
        if event.get("message") == "descendant-ready":
            self.cancellation_requested = True


class BrokenRenderer(CollectRenderer):
    def start(self, context):
        raise RuntimeError("renderer unavailable")


class BrokenFinishRenderer(CollectRenderer):
    def finish(self, summary):
        raise RuntimeError("recap unavailable")


class BrokenFinishSession(EphemeralSession):
    def finish(self, **values):
        del values
        raise OSError("manifest unavailable")


def _delayed_custody_frontend(
    root_text: str,
    marker_text: str,
    canary_text: str,
) -> None:
    root = Path(root_text)
    marker = Path(marker_text)
    canary = Path(canary_text)

    class DelayedBindSession(RetainedSession):
        def bind_process(self, process_id: int, process_group_id: int | None) -> None:
            with marker.open("w", encoding="utf-8") as output:
                output.write(f"{process_id} {process_group_id or process_id}\n")
                output.flush()
                os.fsync(output.fileno())
            time.sleep(30)
            super().bind_process(process_id, process_group_id)

    session = DelayedBindSession(
        root=root,
        command_id="fixture.pre-custody-kill",
        argv=[sys.executable, "-c", "target"],
        cwd=root,
        intent="execute",
        session_id="pre-custody-kill-session",
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(canary)!r}).write_text('executed', encoding='utf-8'); "
        "time.sleep(30)"
    )
    supervise_process(
        [sys.executable, "-c", script],
        cwd=root,
        root=root,
        session=session,
        renderer=CollectRenderer(),
        source="fixture",
    )


class RunnerTests(unittest.TestCase):
    def test_frontend_death_before_custody_release_never_executes_target(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX process-gate crash probe")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            marker = root / "spawned-process.txt"
            canary = root / "target-executed.txt"
            frontend = multiprocessing.get_context("fork").Process(
                target=_delayed_custody_frontend,
                args=(str(root), str(marker), str(canary)),
            )
            frontend.start()
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "launch gate was not spawned")
            gate_pid, process_group = map(
                int, marker.read_text(encoding="utf-8").split()
            )
            os.kill(frontend.pid, signal.SIGKILL)
            frontend.join(10)
            self.assertEqual(frontend.exitcode, -signal.SIGKILL)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        os.kill(gate_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    self.fail("unreleased launch gate survived frontend death")
                self.assertFalse(canary.exists(), "target executed before custody")
                owner = live_console_owner_reference(
                    root, "pre-custody-kill-session"
                )
                self.assertEqual(owner["last_verified_state"], "allocated")
                manifest = json.loads(
                    (
                        root
                        / ".workbench/sessions/live-console/pre-custody-kill-session/session-v1.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertIsNone(manifest["command"]["pid"])
                self.assertIsNone(manifest["command"]["process_group_id"])
            finally:
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_retains_separate_exact_streams_and_valid_events(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            renderer = CollectRenderer()
            session = RetainedSession(
                root=root,
                command_id="fixture.streams",
                argv=[sys.executable, "-c", "fixture"],
                cwd=root,
                intent="execute",
            )
            script = (
                "import os; "
                "os.write(1,b'out one\\nBUILD SUCCESSFUL in 1s\\n'); "
                "os.write(2,b'err one\\n')"
            )
            result = supervise_process(
                [sys.executable, "-c", script],
                cwd=root,
                root=root,
                session=session,
                renderer=renderer,
                source="fixture",
            )
            self.assertEqual(result.effective_exit_code, 0)
            self.assertEqual((session.directory / "stdout.raw").read_bytes(), b"out one\nBUILD SUCCESSFUL in 1s\n")
            self.assertEqual((session.directory / "stderr.raw").read_bytes(), b"err one\n")
            self.assertIn(b"child-process started", (session.directory / "system.raw").read_bytes())
            retained = list(iter_events(session.directory))
            self.assertEqual([event["sequence"] for event in retained], list(range(1, len(retained) + 1)))
            for event in retained:
                Draft202012Validator(EVENT_SCHEMA).validate(event)
                self.assertNotIn("_resolved_source_locators", event)
            Draft202012Validator(SESSION_SCHEMA).validate(result.session)

    def test_empty_observed_streams_are_explicitly_retained(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="fixture.empty-streams",
                argv=[sys.executable, "-c", "pass"],
                cwd=root,
                intent="execute",
            )
            result = supervise_process(
                [sys.executable, "-c", "pass"],
                cwd=root,
                root=root,
                session=session,
                renderer=CollectRenderer(),
                source="fixture",
            )
            self.assertEqual((session.directory / "stdout.raw").read_bytes(), b"")
            self.assertEqual((session.directory / "stderr.raw").read_bytes(), b"")
            self.assertEqual(
                set(result.session["retention"]["raw_streams"]),
                {"stdout", "stderr", "system"},
            )
            Draft202012Validator(SESSION_SCHEMA).validate(result.session)

    def test_explicit_required_failure_overrides_zero_exit(self) -> None:
        renderer = CollectRenderer()
        result = supervise_process(
            [sys.executable, "-c", "print('WORKBENCH_REQUIRED_CHECK_FAILED fixture')"],
            cwd=ROOT,
            root=ROOT,
            session=EphemeralSession(),
            renderer=renderer,
            source="fixture",
        )
        self.assertEqual(result.process_exit_code, 0)
        self.assertEqual(result.effective_exit_code, 1)
        self.assertEqual(result.outcome, "observed-required-failure")
        self.assertGreaterEqual(result.outcome_failure_events, 1)

    def test_raw_output_preserves_bytes_and_only_emits_lifecycle_events(self) -> None:
        class Capture(EphemeralSession):
            def __init__(self):
                super().__init__()
                self.output = {name: bytearray() for name in ("stdout", "stderr")}

            def write_raw(self, stream, data):
                if stream in self.output:
                    self.output[stream].extend(data)
                return super().write_raw(stream, data)

        output = b"WORKBENCH_REQUIRED_CHECK_FAILED fixture\n\x1b[31m\xff\x00\r"
        error = b"\xfe\x1b[32mnot a console message"
        renderer, session = CollectRenderer(), Capture()
        result = supervise_process(
            [sys.executable, "-c", "import sys; "
             f"sys.stdout.buffer.write({output!r}); sys.stderr.buffer.write({error!r})"],
            cwd=ROOT, root=ROOT, session=session, renderer=renderer,
            source="fixture", output_mode="raw", max_record_bytes=1,
        )
        self.assertEqual(session.output, {"stdout": output, "stderr": error})
        self.assertEqual((result.process_exit_code, result.effective_exit_code), (0, 0))
        self.assertEqual(result.outcome_failure_events, 0)
        self.assertEqual([event["message"] for event in renderer.events], [
            "[workbench] stage child-process started", "[workbench] stage child-process completed",
        ])

    def test_retained_console_cannot_disable_output_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "launched"
            argv = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
            session = RetainedSession(root=root, command_id="fixture.raw-refusal",
                                      argv=argv, cwd=root, intent="execute")
            with self.assertRaisesRegex(RunnerError, "raw process output requires an ephemeral session"):
                supervise_process(argv, cwd=root, root=root, session=session,
                                  renderer=CollectRenderer(), source="fixture", output_mode="raw")
            self.assertFalse(marker.exists())

    def test_arbitrary_error_level_does_not_override_zero_exit(self) -> None:
        renderer = CollectRenderer()
        script = "print('[12:00:00] [main/ERROR] [FML]: handled probe')"
        result = supervise_process(
            [sys.executable, "-c", script],
            cwd=ROOT,
            root=ROOT,
            session=EphemeralSession(),
            renderer=renderer,
            source="fixture",
        )
        self.assertEqual(result.effective_exit_code, 0)
        error = next(event for event in renderer.events if event["severity"] == "error")
        self.assertFalse(error["outcome_failure"])

    def test_nonzero_exit_is_preserved(self) -> None:
        result = supervise_process(
            [sys.executable, "-c", "raise SystemExit(7)"],
            cwd=ROOT,
            root=ROOT,
            session=EphemeralSession(),
            renderer=CollectRenderer(),
            source="fixture",
        )
        self.assertEqual(result.process_exit_code, 7)
        self.assertEqual(result.effective_exit_code, 7)
        self.assertEqual(result.state, "failed")

    def test_cancel_targets_exact_child_group_and_records_cancelled(self) -> None:
        renderer = CancelRenderer()
        result = supervise_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=ROOT,
            root=ROOT,
            session=EphemeralSession(),
            renderer=renderer,
            source="fixture",
            interrupt_grace_seconds=1,
            terminate_grace_seconds=1,
        )
        self.assertEqual(result.state, "cancelled")
        self.assertEqual(result.effective_exit_code, 130)
        self.assertIn(result.cancellation, {"interrupt-requested", "terminated", "forced"})

    @unittest.skipUnless(sys.platform != "win32", "POSIX process-group behavior")
    def test_cancel_escalates_after_group_leader_exits(self) -> None:
        descendant = (
            "import signal,time; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('descendant-ready', flush=True); "
            "time.sleep(30)"
        )
        script = (
            "import signal,subprocess,sys,time\n"
            "def stop(*unused):\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGINT, stop)\n"
            f"subprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
            "time.sleep(30)\n"
        )
        started = time.monotonic()
        result = supervise_process(
            [sys.executable, "-c", script],
            cwd=ROOT,
            root=ROOT,
            session=EphemeralSession(),
            renderer=DescendantCancelRenderer(),
            source="fixture",
            interrupt_grace_seconds=0.1,
            terminate_grace_seconds=0.1,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3)
        self.assertEqual(result.state, "cancelled")
        self.assertEqual(result.cancellation, "forced")

    def test_unique_source_resolution_is_presentation_only(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/Foo.java").write_text("class Foo {}\n", encoding="utf-8")
            renderer = CollectRenderer()
            session = RetainedSession(
                root=root,
                command_id="fixture.source",
                argv=[sys.executable, "fixture"],
                cwd=root,
                intent="execute",
            )
            result = supervise_process(
                [sys.executable, "-c", "print('src/Foo.java:1: error: broken')"],
                cwd=root,
                root=root,
                session=session,
                renderer=renderer,
                source="fixture",
            )
            compiler = next(event for event in renderer.events if event["message"] == "src/Foo.java:1: error: broken")
            self.assertEqual(compiler["_resolved_source_locators"][0]["resolution"], "exact")
            retained = next(event for event in iter_events(session.directory) if event["message"] == "src/Foo.java:1: error: broken")
            self.assertNotIn("_resolved_source_locators", retained)
            Draft202012Validator(EVENT_SCHEMA).validate(retained)
            Draft202012Validator(SESSION_SCHEMA).validate(result.session)

    def test_jsonl_is_only_contract_events(self) -> None:
        output = io.StringIO()
        renderer = JsonlRenderer(output, event_filter=EventFilter(signal_only=False))
        result = supervise_process(
            [sys.executable, "-c", "print('hello')"],
            cwd=ROOT,
            root=ROOT,
            session=EphemeralSession(),
            renderer=renderer,
            source="fixture",
        )
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), result.event_count)
        for row in rows:
            self.assertFalse(any(key.startswith("_") for key in row))
            Draft202012Validator(EVENT_SCHEMA).validate(row)

    def test_ingest_retains_every_input_byte_and_preserves_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            log = root / "latest.log"
            raw = b"loading\r\nWORKBENCH_REQUIRED_CHECK_FAILED fixture\n"
            log.write_bytes(raw)
            session = RetainedSession(
                root=root,
                command_id="console.ingest",
                argv=["ingest", str(log)],
                cwd=root,
                intent="inspect",
            )
            result = ingest_files(
                [log], root=root, session=session, renderer=CollectRenderer()
            )
            self.assertEqual(result.effective_exit_code, 1)
            self.assertEqual((session.directory / "ingest-001.raw").read_bytes(), raw)
            Draft202012Validator(SESSION_SCHEMA).validate(result.session)

    def test_empty_ingested_file_is_an_observed_raw_stream(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            empty = root / "empty.log"
            empty.write_bytes(b"")
            session = RetainedSession(
                root=root,
                command_id="console.ingest",
                argv=["ingest", str(empty)],
                cwd=root,
                intent="inspect",
            )
            result = ingest_files(
                [empty], root=root, session=session, renderer=CollectRenderer()
            )
            self.assertEqual((session.directory / "ingest-001.raw").read_bytes(), b"")
            Draft202012Validator(SESSION_SCHEMA).validate(result.session)

    def test_blank_and_default_maximum_records_remain_sealed_and_navigable(self) -> None:
        samples = {
            "blank": b"\n",
            "long": b"x" * 8193,
            "default-maximum-crlf": b"y" * (256 * 1024) + b"\r\n",
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            for index, (label, raw) in enumerate(samples.items(), 1):
                with self.subTest(label=label):
                    log = root / f"{label}.log"
                    log.write_bytes(raw)
                    session = RetainedSession(
                        root=root,
                        command_id=f"console.ingest-{index}",
                        argv=["ingest", str(log)],
                        cwd=root,
                        intent="inspect",
                        session_id=f"ingest-boundary-{index}",
                    )
                    result = ingest_files(
                        [log],
                        root=root,
                        session=session,
                        renderer=CollectRenderer(),
                    )
                    self.assertEqual(result.state, "complete")
                    owner = live_console_owner_reference(root, session.session_id)
                    listed = read_live_console_owner_artifacts(
                        root,
                        owner,
                        limit=1024,
                    )
                    source_events = [
                        row
                        for row in listed["events"]
                        if row["stream"] == "ingest-001"
                    ]
                    self.assertTrue(source_events)
                    selected_bytes: list[bytes] = []
                    for source_event in source_events:
                        selected = read_live_console_owner_artifacts(
                            root,
                            owner,
                            event_id=source_event["event_id"],
                        )
                        payload = base64.b64decode(selected["content_base64"])
                        self.assertEqual(
                            payload,
                            raw[
                                source_event["byte_start"]
                                : source_event["byte_end"]
                            ],
                        )
                        selected_bytes.append(payload)
                    self.assertEqual(b"".join(selected_bytes), raw)

    def test_launch_and_renderer_failures_leave_terminal_manifests(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            for index, (argv, renderer) in enumerate(
                [
                    ([str(root / "missing-executable")], CollectRenderer()),
                    ([sys.executable, "-c", "print('never')"], BrokenRenderer()),
                ]
            ):
                session = RetainedSession(
                    root=root,
                    command_id=f"fixture.failure-{index}",
                    argv=argv,
                    cwd=root,
                    intent="execute",
                )
                with self.assertRaisesRegex(Exception, "live-console"):
                    supervise_process(
                        argv,
                        cwd=root,
                        root=root,
                        session=session,
                        renderer=renderer,
                        source="fixture",
                    )
                manifest = json.loads((session.directory / "session-v1.json").read_text())
                self.assertEqual(manifest["state"], "failed")
                self.assertEqual(manifest["exit"]["effective_exit_code"], 2)
                Draft202012Validator(SESSION_SCHEMA).validate(manifest)

    def test_recap_failure_does_not_leave_a_running_session(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="fixture.recap-failure",
                argv=[sys.executable, "-c", "print('complete')"],
                cwd=root,
                intent="execute",
            )
            renderer = BrokenFinishRenderer()
            with self.assertRaisesRegex(Exception, "after retained completion"):
                supervise_process(
                    [sys.executable, "-c", "print('complete')"],
                    cwd=root,
                    root=root,
                    session=session,
                    renderer=renderer,
                    source="fixture",
                )
            self.assertTrue(renderer.closed)
            manifest = json.loads((session.directory / "session-v1.json").read_text())
            self.assertEqual(manifest["state"], "complete")
            Draft202012Validator(SESSION_SCHEMA).validate(manifest)

    def test_session_finalization_failure_still_closes_renderer(self) -> None:
        renderer = CollectRenderer()
        with self.assertRaisesRegex(Exception, "session finalization failed"):
            supervise_process(
                [sys.executable, "-c", "pass"],
                cwd=ROOT,
                root=ROOT,
                session=BrokenFinishSession(),
                renderer=renderer,
                source="fixture",
            )
        self.assertTrue(renderer.closed)

    def test_ingest_rejects_more_streams_than_v1_can_retain(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            paths = []
            for index in range(16):
                path = root / f"input-{index}.log"
                path.write_text("line\n", encoding="utf-8")
                paths.append(path)
            session = RetainedSession(
                root=root,
                command_id="console.ingest",
                argv=["ingest", *(str(path) for path in paths)],
                cwd=root,
                intent="inspect",
            )
            with self.assertRaisesRegex(Exception, "at most 15 files"):
                ingest_files(
                    paths,
                    root=root,
                    session=session,
                    renderer=CollectRenderer(),
                )
            manifest = json.loads((session.directory / "session-v1.json").read_text())
            self.assertEqual(manifest["state"], "failed")
            Draft202012Validator(SESSION_SCHEMA).validate(manifest)

    def test_invalid_prelaunch_argv_terminalizes_existing_session(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="external.watch",
                argv=["fixture"],
                cwd=root,
                intent="execute",
            )
            with self.assertRaisesRegex(Exception, "process validation failed"):
                supervise_process(
                    [""],
                    cwd=root,
                    root=root,
                    session=session,
                    renderer=CollectRenderer(),
                    source="fixture",
                )
            manifest = json.loads((session.directory / "session-v1.json").read_text())
            self.assertEqual(manifest["state"], "failed")
            Draft202012Validator(SESSION_SCHEMA).validate(manifest)


if __name__ == "__main__":
    unittest.main()
