from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from workbench_core.sessions import (
    EVENTS_NAME,
    MANIFEST_NAME,
    MANIFEST_REVISIONS_DIRECTORY,
    RetainedSession,
    SessionError,
    iter_events,
    live_console_execution_reference,
    live_console_owner_reference,
    list_sessions,
    resolve_session,
    resolve_live_console_owner_reference,
    read_live_console_owner_artifacts,
    verify_live_console_manifest_chain,
)


def event(
    sequence: int,
    *,
    stream: str,
    byte_start: int,
    byte_end: int,
    severity: str = "info",
    failure: bool = False,
):
    return {
        "format_version": "workbench-live-console-event-v1",
        "event_id": f"event:{sequence}",
        "sequence": sequence,
        "ingested_at": "2026-08-04T00:00:00Z",
        "monotonic_ns": sequence,
        "source_timestamp": None,
        "source": "test",
        "stream": stream,
        "raw_locator": {
            "artifact": f"{stream}.raw",
            "byte_start": byte_start,
            "byte_end": byte_end,
            "line": sequence,
            "chunk": 1,
            "boundary": "lf",
        },
        "kind": "log",
        "severity": severity,
        "subsystem": "generic",
        "logger": None,
        "thread": None,
        "message": f"line {sequence}",
        "parse_provenance": "raw",
        "classification_basis": [],
        "cluster_key": "sha256:" + f"{sequence:064x}",
        "signal": False,
        "outcome_failure": failure,
        "source_locators": [],
        "limitations": [],
    }


def start_bound_zero_exit_process(
    session: RetainedSession,
    root: Path,
    *,
    exit_code: int = 0,
) -> tuple[subprocess.Popen[bytes], Path]:
    ready = root / f"{session.session_id}.ready"
    release = root / f"{session.session_id}.release"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time\n"
                "ready=pathlib.Path(" + repr(str(ready)) + ")\n"
                "release=pathlib.Path(" + repr(str(release)) + ")\n"
                "ready.write_text('ready\\n', encoding='utf-8')\n"
                "while not release.exists(): time.sleep(0.01)\n"
                "raise SystemExit(" + repr(exit_code) + ")\n"
            ),
        ],
        start_new_session=os.name == "posix",
    )
    deadline = time.monotonic() + 10
    while not ready.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            process.kill()
            process.wait(timeout=10)
            raise AssertionError("fixture child did not become ready")
        time.sleep(0.01)
    session.bind_process(
        process.pid,
        process.pid if os.name == "posix" else None,
    )
    return process, release


class SessionTests(unittest.TestCase):
    def test_packaged_suite_sessions_use_external_runtime_state_only_at_suite_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            suite = root / "installed-suite"
            selected = root / "selected-state"
            state_base = root / "product-spine"
            suite.mkdir()
            state_base.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "WORKBENCH_PACKAGED_SUITE_ROOT": str(suite),
                    "WORKBENCH_STATE_ROOT": str(selected),
                },
                clear=True,
            ):
                suite_session = RetainedSession(
                    root=suite,
                    command_id="test.packaged-suite",
                    argv=["fixture"],
                    cwd=suite,
                    intent="inspect",
                    session_id="packaged-suite-session-001",
                )
                self.assertEqual(
                    selected / "sessions/live-console/packaged-suite-session-001",
                    suite_session.directory,
                )
                suite_session.abort("fixture complete")

                state_session = RetainedSession(
                    root=state_base,
                    command_id="test.product-state",
                    argv=["fixture"],
                    cwd=state_base,
                    intent="inspect",
                    session_id="product-state-session-001",
                )
                self.assertEqual(
                    state_base
                    / ".workbench/sessions/live-console/product-state-session-001",
                    state_session.directory,
                )
                state_session.abort("fixture complete")

    def test_terminal_seal_rejects_same_size_event_and_raw_rewrites(self) -> None:
        for target in ("event", "raw"):
            with self.subTest(target=target), tempfile.TemporaryDirectory(
                dir="/tmp"
            ) as temporary:
                root = Path(temporary)
                session = RetainedSession(
                    root=root,
                    command_id="test.command",
                    argv=["fixture"],
                    cwd=root,
                    intent="inspect",
                    session_id=f"producer-tamper-{target}",
                )
                session.write_raw("stdout", b"good\n")
                session.record_event(
                    event(1, stream="stdout", byte_start=0, byte_end=5)
                )
                if target == "event":
                    event_path = session.directory / EVENTS_NAME
                    retained = json.loads(event_path.read_text(encoding="utf-8"))
                    self.assertEqual(len(retained["message"]), len("evil 1"))
                    retained["message"] = "evil 1"
                    event_path.write_text(
                        json.dumps(
                            retained,
                            ensure_ascii=True,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    event_path.chmod(0o600)
                else:
                    raw_path = session.directory / "stdout.raw"
                    raw_path.write_bytes(b"evil\n")
                    raw_path.chmod(0o600)
                with self.assertRaisesRegex(
                    SessionError, "producer-observed|durably finalize"
                ):
                    session.finish(
                        state="complete",
                        process_exit_code=None,
                        effective_exit_code=0,
                        outcome="complete",
                    )
                manifest = json.loads(
                    (session.directory / MANIFEST_NAME).read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["state"], "incomplete")
                self.assertEqual(manifest["exit"]["outcome"], "retention-error")
                self.assertFalse((session.directory / "artifact-index-v1.json").exists())

    def test_terminal_tuple_is_derived_from_events_and_process_custody(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            observed_failure = RetainedSession(
                root=root,
                command_id="test.inspect",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="outcome-failure-session",
            )
            observed_failure.write_raw("stdout", b"failure\n")
            observed_failure.record_event(
                event(
                    1,
                    stream="stdout",
                    byte_start=0,
                    byte_end=8,
                    severity="fatal",
                    failure=True,
                )
            )
            with self.assertRaisesRegex(SessionError, "complete terminal tuple"):
                observed_failure.finish(
                    state="complete",
                    process_exit_code=None,
                    effective_exit_code=0,
                    outcome="complete",
                )
            failed = observed_failure.finish(
                state="failed",
                process_exit_code=None,
                effective_exit_code=1,
                outcome="observed-required-failure",
            )
            self.assertEqual(failed["state"], "failed")

            missing_process = RetainedSession(
                root=root,
                command_id="test.execute",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="missing-process-session",
            )
            with self.assertRaisesRegex(SessionError, "no exact process result"):
                missing_process.finish(
                    state="complete",
                    process_exit_code=None,
                    effective_exit_code=0,
                    outcome="complete",
                )
            missing_process.finish(
                state="failed",
                process_exit_code=None,
                effective_exit_code=2,
                outcome="launch-failed",
            )

            nonzero = RetainedSession(
                root=root,
                command_id="test.execute",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="nonzero-process-session",
            )
            process, release = start_bound_zero_exit_process(
                nonzero,
                root,
                exit_code=7,
            )
            release.write_text("go\n", encoding="utf-8")
            process.wait(timeout=10)
            with self.assertRaisesRegex(SessionError, "complete terminal tuple"):
                nonzero.finish(
                    state="complete",
                    process_exit_code=process.returncode,
                    effective_exit_code=0,
                    outcome="complete",
                )
            nonzero.finish(
                state="failed",
                process_exit_code=process.returncode,
                effective_exit_code=7,
                outcome="process-failed",
            )

    def test_owner_artifact_range_uses_historical_chain_and_terminal_seal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="owner-artifact-session",
            )
            historical = live_console_owner_reference(root, session.session_id)
            locator = session.write_raw("stdout", b"hello\n")
            session.record_event(
                {
                    "format_version": "workbench-live-console-event-v1",
                    "event_id": "event:1",
                    "sequence": 1,
                    "ingested_at": "2026-08-21T00:00:00Z",
                    "monotonic_ns": 1,
                    "source_timestamp": None,
                    "source": "test.command",
                    "stream": "stdout",
                    "raw_locator": {
                        "artifact": locator.path,
                        "byte_start": locator.byte_start,
                        "byte_end": locator.byte_end,
                        "line": 1,
                        "chunk": 1,
                        "boundary": "lf",
                    },
                    "kind": "text",
                    "severity": "info",
                    "subsystem": "generic",
                    "logger": None,
                    "thread": None,
                    "message": "hello",
                    "parse_provenance": "raw",
                    "classification_basis": [],
                    "cluster_key": "sha256:" + "0" * 64,
                    "signal": False,
                    "outcome_failure": False,
                    "source_locators": [],
                    "limitations": [],
                }
            )
            session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )

            listed = read_live_console_owner_artifacts(root, historical, limit=1)
            self.assertEqual(listed["format_version"], "workbench-owner-artifact-events-v1")
            self.assertEqual(listed["owner_digest"], historical["digest"])
            self.assertNotEqual(listed["current_owner_digest"], historical["digest"])
            self.assertEqual([row["event_id"] for row in listed["events"]], ["event:1"])
            self.assertFalse(listed["has_more"])
            self.assertEqual(listed["next_after_sequence"], 1)

            selected = read_live_console_owner_artifacts(
                root,
                historical,
                event_id="event:1",
            )
            self.assertEqual(selected["format_version"], "workbench-owner-artifact-range-v1")
            self.assertEqual(selected["content_base64"], "aGVsbG8K")
            self.assertEqual(selected["utf8"], "hello\n")
            self.assertEqual(selected["byte_count"], 6)

            raw_path = session.directory / "stdout.raw"
            raw_path.write_bytes(b"jello\n")
            raw_path.chmod(0o600)
            with self.assertRaisesRegex(SessionError, "sealed index"):
                read_live_console_owner_artifacts(
                    root,
                    historical,
                    event_id="event:1",
                )

    def test_owner_reference_reproduces_allocated_running_and_terminal_custody(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="owner-custody-session",
            )
            allocated = live_console_owner_reference(root, session.session_id)
            self.assertEqual(allocated["last_verified_state"], "allocated")
            self.assertEqual(allocated["record_id"], session.session_id)
            self.assertEqual(allocated["record_kind"], "workbench-live-console-session-v1")
            self.assertTrue(allocated["uri"].endswith("/session-v1.json"))
            allocated_proof = verify_live_console_manifest_chain(
                root, session.session_id, prior_digest=allocated["digest"]
            )
            self.assertEqual(allocated_proof["revision_count"], 1)
            self.assertEqual(allocated_proof["prior_revision"], 0)
            self.assertIsNone(allocated_proof["process_custody"])
            execution = live_console_execution_reference(root, allocated)
            self.assertEqual(
                {key: value for key, value in execution["owner_record_ref"].items() if key != "verified_at"},
                {key: value for key, value in allocated.items() if key != "verified_at"},
            )
            self.assertEqual(execution["command"]["command_id"], "test.command")
            self.assertEqual(execution["command"]["argv"], ["fixture"])

            process, release = start_bound_zero_exit_process(session, root)
            running = resolve_live_console_owner_reference(root, allocated)
            self.assertEqual(running["last_verified_state"], "running")
            self.assertNotEqual(running["digest"], allocated["digest"])
            running_proof = verify_live_console_manifest_chain(
                root, session.session_id, prior_digest=allocated["digest"]
            )
            self.assertEqual(running_proof["revision_count"], 2)
            self.assertEqual(running_proof["prior_revision"], 0)
            self.assertEqual(running_proof["current_digest"], running["digest"])
            self.assertEqual(running_proof["process_custody"]["pid"], process.pid)

            release.write_text("go\n", encoding="utf-8")
            process.wait(timeout=10)

            session.finish(
                state="complete",
                process_exit_code=process.returncode,
                effective_exit_code=0,
                outcome="complete",
            )
            complete = live_console_owner_reference(root, session.session_id)
            self.assertEqual(complete["last_verified_state"], "complete")
            self.assertNotEqual(complete["digest"], running["digest"])
            complete_proof = verify_live_console_manifest_chain(
                root, session.session_id, prior_digest=running["digest"]
            )
            self.assertEqual(complete_proof["revision_count"], 3)
            self.assertEqual(complete_proof["prior_revision"], 1)
            self.assertEqual(
                complete_proof["command_identity"],
                running_proof["command_identity"],
            )
            self.assertEqual(
                complete_proof["process_custody"],
                running_proof["process_custody"],
            )

    def test_schema_valid_manifest_replacement_without_revision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="unrecorded-replacement-session",
            )
            path = session.directory / MANIFEST_NAME
            replacement = json.loads(path.read_text(encoding="utf-8"))
            replacement["limitations"].append(
                "This replacement remains V1-schema-valid but has no revision record."
            )
            path.write_text(
                json.dumps(replacement, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(SessionError, "chain|revision|digest"):
                live_console_owner_reference(root, session.session_id)
            with self.assertRaisesRegex(SessionError, "chain|revision|digest"):
                session.abort("test cleanup")

    def test_manifest_revision_chain_rejects_missing_reordered_truncated_and_symlink_rows(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        for damage in ("missing", "reordered", "truncated", "symlink"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory(
                dir="/tmp"
            ) as temporary:
                root = Path(temporary)
                session = RetainedSession(
                    root=root,
                    command_id="test.command",
                    argv=["fixture"],
                    cwd=root,
                    intent="execute",
                    session_id=f"chain-{damage}-session",
                )
                process, release = start_bound_zero_exit_process(session, root)
                release.write_text("go\n", encoding="utf-8")
                process.wait(timeout=10)
                session.finish(
                    state="complete",
                    process_exit_code=process.returncode,
                    effective_exit_code=0,
                    outcome="complete",
                )
                chain = session.directory / MANIFEST_REVISIONS_DIRECTORY
                rows = sorted(chain.glob("*.json"))
                self.assertEqual(len(rows), 3)
                if damage == "missing":
                    rows[1].unlink()
                elif damage == "reordered":
                    first = rows[0].read_bytes()
                    second = rows[1].read_bytes()
                    rows[0].write_bytes(second)
                    rows[1].write_bytes(first)
                elif damage == "truncated":
                    rows[1].write_bytes(b'{"format_version":')
                else:
                    replacement = session.directory / "replacement-revision.json"
                    replacement.write_bytes(rows[1].read_bytes())
                    replacement.chmod(0o600)
                    rows[1].unlink()
                    rows[1].symlink_to(replacement)
                with self.assertRaisesRegex(
                    SessionError, "chain|revision|canonical|unsafe|missing"
                ):
                    verify_live_console_manifest_chain(root, session.session_id)

    def test_process_start_identity_mismatch_cannot_claim_running(self) -> None:
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux /proc custody probe")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="pid-reuse-session",
            )
            session.bind_process(os.getpid(), None)
            proof = verify_live_console_manifest_chain(root, session.session_id)
            reused = dict(proof["process_custody"])
            reused["process_start_time_ticks"] = str(
                int(reused["process_start_time_ticks"]) + 1
            )
            with mock.patch(
                "workbench_core.sessions._capture_process_custody",
                return_value=reused,
            ):
                current = live_console_owner_reference(root, session.session_id)
            self.assertEqual(current["last_verified_state"], "incomplete")
            session.finish(
                state="incomplete",
                process_exit_code=None,
                effective_exit_code=2,
                outcome="console-error",
            )

    def test_process_binding_fails_when_custody_is_unverifiable(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="unverifiable-process-session",
            )
            with mock.patch(
                "workbench_core.sessions._capture_process_custody",
                side_effect=SessionError("process custody is unverifiable"),
            ):
                with self.assertRaisesRegex(SessionError, "unverifiable"):
                    session.bind_process(os.getpid(), None)
            current = live_console_owner_reference(root, session.session_id)
            self.assertEqual(current["last_verified_state"], "allocated")
            session.finish(
                state="incomplete",
                process_exit_code=None,
                effective_exit_code=2,
                outcome="console-error",
            )

    def test_terminal_manifest_is_single_assignment(self) -> None:
        for replacement_state in ("failed", "cancelled"):
            with self.subTest(replacement_state=replacement_state), tempfile.TemporaryDirectory(
                dir="/tmp"
            ) as temporary:
                root = Path(temporary)
                session = RetainedSession(
                    root=root,
                    command_id="test.command",
                    argv=["fixture"],
                    cwd=root,
                    intent="inspect",
                    session_id=f"terminal-rewrite-{replacement_state}",
                )
                session.finish(
                    state="complete",
                    process_exit_code=None,
                    effective_exit_code=0,
                    outcome="complete",
                )
                before = verify_live_console_manifest_chain(root, session.session_id)
                with self.assertRaisesRegex(SessionError, "single-assignment"):
                    session.finish(
                        state=replacement_state,
                        process_exit_code=1,
                        effective_exit_code=1,
                        outcome=replacement_state,
                    )
                after = verify_live_console_manifest_chain(root, session.session_id)
                self.assertEqual(after, before)
                manifest = json.loads(
                    (session.directory / MANIFEST_NAME).read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["state"], "complete")

    def test_owner_reference_rejects_noncanonical_or_symlinked_manifest(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="owner-manifest-session",
            )
            path = session.directory / MANIFEST_NAME
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(SessionError, "producer-canonical"):
                live_console_owner_reference(root, session.session_id)
            with self.assertRaisesRegex(SessionError, "producer-canonical"):
                session.abort("test cleanup")

            if hasattr(os, "symlink"):
                replacement = session.directory / "replacement.json"
                replacement.write_text(
                    json.dumps(session.value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                path.unlink()
                path.symlink_to(replacement)
                with self.assertRaisesRegex(SessionError, "unsafe"):
                    live_console_owner_reference(root, session.session_id)

    def test_owner_reference_rejects_widened_permissions_and_unknown_fields(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX owner-private mode probe")
        for target in ("directory", "manifest"):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir="/tmp") as temporary:
                root = Path(temporary)
                session = RetainedSession(
                    root=root,
                    command_id="test.command",
                    argv=["fixture"],
                    cwd=root,
                    intent="inspect",
                    session_id=f"privacy-{target}-session",
                )
                session.finish(
                    state="complete",
                    process_exit_code=None,
                    effective_exit_code=0,
                    outcome="complete",
                )
                path = (
                    session.directory
                    if target == "directory"
                    else session.directory / MANIFEST_NAME
                )
                path.chmod(0o755 if target == "directory" else 0o644)
                with self.assertRaisesRegex(SessionError, "owner-private"):
                    live_console_owner_reference(root, session.session_id)

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="unknown-field-session",
            )
            session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )
            path = session.directory / MANIFEST_NAME
            value = json.loads(path.read_text(encoding="utf-8"))
            value["invented_success"] = True
            path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(SessionError, "fields"):
                live_console_owner_reference(root, session.session_id)

    def test_retains_exact_raw_streams_and_projection(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["python", "fixture.py"],
                cwd=root,
                intent="inspect",
            )
            first = session.write_raw("stdout", b"hello ")
            second = session.write_raw("stdout", b"world\n")
            error = session.write_raw("stderr", b"warning\n")
            self.assertEqual((first.byte_start, first.byte_end), (0, 6))
            self.assertEqual((second.byte_start, second.byte_end), (6, 12))
            self.assertEqual((error.byte_start, error.byte_end), (0, 8))
            session.record_event(
                event(1, stream="stdout", byte_start=0, byte_end=12)
            )
            session.record_event(
                event(
                    2,
                    stream="stderr",
                    byte_start=0,
                    byte_end=8,
                    severity="fatal",
                    failure=True,
                )
            )
            manifest = session.finish(
                state="failed",
                process_exit_code=None,
                effective_exit_code=1,
                outcome="observed-required-failure",
            )
            self.assertEqual((session.directory / "stdout.raw").read_bytes(), b"hello world\n")
            self.assertEqual((session.directory / "stderr.raw").read_bytes(), b"warning\n")
            self.assertEqual(len(list(iter_events(session.directory))), 2)
            self.assertEqual(manifest["summary"]["outcome_failure_events"], 1)
            self.assertIsNone(manifest["exit"]["process_exit_code"])
            self.assertEqual(manifest["exit"]["effective_exit_code"], 1)

    def test_manifest_starts_running_and_files_are_private(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX permission assertions")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
            )
            manifest = json.loads((session.directory / MANIFEST_NAME).read_text())
            self.assertEqual(manifest["state"], "running")
            self.assertEqual(session.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((session.directory / EVENTS_NAME).stat().st_mode & 0o777, 0o600)
            session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )

    def test_list_and_prefix_resolution(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="session-unique-001",
            )
            session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )
            self.assertEqual(list_sessions(root)[0]["session_id"], "session-unique-001")
            directory, value = resolve_session(root, "session-unique")
            self.assertEqual(directory, session.directory)
            self.assertEqual(value["state"], "complete")

    def test_symlinked_session_storage_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary) / "root"
            outside = Path(temporary) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".workbench").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(SessionError, "symlink"):
                RetainedSession(
                    root=root,
                    command_id="test.command",
                    argv=["fixture"],
                    cwd=root,
                    intent="execute",
                )

    def test_raw_stream_count_is_bounded_by_the_session_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
            )
            for index in range(16):
                session.write_raw(f"stream-{index}", b"x")
            with self.assertRaisesRegex(SessionError, "at most 16 raw streams"):
                session.write_raw("overflow", b"x")
            session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )

    def test_fsync_failure_cannot_publish_a_complete_session(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
            )
            session.write_raw("stdout", b"complete\n")
            real_fsync = os.fsync
            calls = 0

            def fail_once(descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("simulated durability failure")
                real_fsync(descriptor)

            with mock.patch("workbench_core.sessions.os.fsync", side_effect=fail_once):
                with self.assertRaisesRegex(SessionError, "durably finalize"):
                    session.finish(
                        state="complete",
                        process_exit_code=None,
                        effective_exit_code=0,
                        outcome="complete",
                    )
            manifest = json.loads((session.directory / MANIFEST_NAME).read_text())
            self.assertEqual(manifest["state"], "incomplete")
            self.assertEqual(manifest["exit"]["outcome"], "retention-error")
            self.assertEqual(manifest["exit"]["effective_exit_code"], 2)

    def test_session_metadata_rejects_multiline_label_but_preserves_exact_argv(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(SessionError, "single-line"):
                RetainedSession(
                    root=root,
                    command_id="test.command",
                    argv=["fixture"],
                    cwd=root,
                    intent="execute",
                    label="bad\nlabel",
                )
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture", "line\nbreak", ""],
                cwd=root,
                intent="inspect",
            )
            manifest = session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )
            self.assertEqual(manifest["command"]["argv"], ["fixture", "line\nbreak", ""])

    def test_malformed_and_tampered_manifests_cannot_redirect_replay(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            base = root / ".workbench/sessions/live-console"
            malformed = base / "malformed-session"
            malformed.mkdir(parents=True)
            (malformed / MANIFEST_NAME).write_text("[]\n", encoding="utf-8")
            session = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="tampered-session",
            )
            session.finish(
                state="complete",
                process_exit_code=None,
                effective_exit_code=0,
                outcome="complete",
            )
            path = session.directory / MANIFEST_NAME
            value = json.loads(path.read_text())
            value["retention"]["directory"] = "/tmp"
            path.write_text(json.dumps(value), encoding="utf-8")

            listed = {item["session_id"]: item for item in list_sessions(root)}
            self.assertEqual(listed["malformed-session"]["state"], "incomplete")
            self.assertEqual(listed["tampered-session"]["state"], "incomplete")
            directory, _ = resolve_session(root, "tampered-session")
            self.assertEqual(directory, session.directory)

    def test_dead_running_session_is_presented_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            dead = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="dead-running-session",
            )
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=os.name == "posix",
            )
            try:
                dead.bind_process(
                    process.pid,
                    process.pid if os.name == "posix" else None,
                )
            finally:
                process.terminate()
                process.wait(timeout=10)
            self.assertEqual(list_sessions(root)[0]["state"], "incomplete")
            dead.finish(
                state="incomplete",
                process_exit_code=None,
                effective_exit_code=2,
                outcome="console-error",
            )

    def test_live_running_session_remains_running(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            live = RetainedSession(
                root=root,
                command_id="test.command",
                argv=["fixture"],
                cwd=root,
                intent="execute",
                session_id="live-running-session",
            )
            process, release = start_bound_zero_exit_process(live, root)
            self.assertEqual(list_sessions(root)[0]["state"], "running")
            release.write_text("go\n", encoding="utf-8")
            process.wait(timeout=10)
            live.finish(
                state="complete",
                process_exit_code=process.returncode,
                effective_exit_code=0,
                outcome="complete",
            )


if __name__ == "__main__":
    unittest.main()
