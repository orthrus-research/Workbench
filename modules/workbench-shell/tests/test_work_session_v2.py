from __future__ import annotations

import json
from hashlib import sha256
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/workbench-shell/src",
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/crucible/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.catalog import build_catalog
import workbench_shell.work_session as work_session
from workbench_shell.work_session import (
    JOURNAL_DIRECTORY_NAME,
    SESSION_RECORD_NAME,
    SUMMARY_NAME,
    WorkSessionConflictError,
    WorkSessionError,
    WorkSessionStore,
    validate_work_session_event,
    validate_work_session_summary,
    work_session_recovery_action,
)
from workbench_api.host_filesystem import HostFilesystemError


FRONTEND = {
    "frontend_id": "frontend:test-cli",
    "kind": "cli",
    "version": "test",
}
VSCODE_FRONTEND = {
    "frontend_id": "frontend:test-vscode",
    "kind": "vscode",
    "version": "test",
}
SCHEMA_ROOT = ROOT / "modules/workbench-shell/schemas"


def _append_from_process(
    root_text: str,
    session_id: str,
    gate: multiprocessing.synchronize.Event,
    output: multiprocessing.queues.Queue,
    index: int,
) -> None:
    store = WorkSessionStore(Path(root_text))
    gate.wait(10)
    try:
        result = store.append(
            session_id,
            expected_sequence=0,
            frontend={
                "frontend_id": f"frontend:process-{index}",
                "kind": "cli",
                "version": "test",
            },
            kind="concurrent-append",
            message=f"process {index}",
        )
    except WorkSessionError as exc:
        output.put(("error", exc.code))
    else:
        output.put(("ok", result["event"]["sequence"]))


def _exit_after_event_publish(root_text: str, session_id: str) -> None:
    def crash(checkpoint: str) -> None:
        if checkpoint == "after-event-publish":
            os._exit(77)

    store = WorkSessionStore(Path(root_text), fault_injector=crash)
    store.append(
        session_id,
        expected_sequence=0,
        frontend=FRONTEND,
        kind="build-started",
        lifecycle="attention",
        message="frontend published an attention event",
    )


def _trusted_owner_reference(reference):
    """Test adapter standing in for the exact record-owning service."""

    return dict(reference)


def _trusted_owner_result(result):
    """Test adapter reproducing one exact owner-returned result envelope."""

    return dict(result)


def _trusted_owner_execution(prepared):
    """Return a verifier that reproduces the prepared command custody."""

    def verify(reference):
        return {
            "owner_record_ref": dict(reference),
            "command": {
                "command_id": prepared["action"]["action_id"],
                "argv": list(prepared["argv"]),
                "cwd": prepared["workspace"]["canonical_root"],
                "intent": prepared["intent"],
                "shell": False,
            },
        }

    return verify


def _frontend_crash_with_owned_child(
    root_text: str,
    session_id: str,
    pid_path_text: str,
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_path = Path(pid_path_text)
    with pid_path.open("w", encoding="utf-8") as output:
        output.write(str(child.pid))
        output.flush()
        os.fsync(output.fileno())

    def crash(checkpoint: str) -> None:
        if checkpoint == "after-event-publish":
            os._exit(78)

    store = WorkSessionStore(Path(root_text), fault_injector=crash)
    owner_ref = {
        "owner_id": "crucible",
        "record_id": f"process-instance:{child.pid}",
        "record_kind": "process-custody",
        "uri": f"process://{child.pid}",
        "digest": "sha256:" + "1" * 64,
        "last_verified_state": "running",
        "verified_at": "2026-08-21T12:00:00Z",
    }
    store.mark_owner_recoverable(
        session_id,
        expected_sequence=0,
        frontend=FRONTEND,
        reason="frontend exited while the exact child remained owner-custodied",
        owner_record_refs=[owner_ref],
        owner_reference_verifier=_trusted_owner_reference,
        next_actions=[work_session_recovery_action(session_id)],
    )


class WorkSessionV2Tests(unittest.TestCase):
    def _create(
        self,
        root: Path,
        *,
        store: WorkSessionStore | None = None,
        catalog_id: str = "catalog:test",
    ):
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        selected = store or WorkSessionStore(root)
        created = selected.create(
            task={"task_id": "task:test", "owner_id": "workbench-shell"},
            workspace={
                "identity_id": "workspace:test",
                "canonical_root": str(workspace),
                "source_revision": "git:test",
                "dirty_fingerprint": None,
            },
            identities={
                "core_id": "core:test",
                "catalog_id": catalog_id,
                "host_adapter_id": "host:test",
                "platform_profile_id": "platform:test",
                "pack_profile_id": None,
            },
            frontend=FRONTEND,
        )
        return selected, created

    def _bind_running(self, root: Path, *, suffix: str = "terminal"):
        """Create one real catalog preparation and bind exact owner custody."""

        catalog = build_catalog(ROOT)
        store, created = self._create(root, catalog_id=catalog.catalog_digest)
        command = catalog.command("doctor.inspect")
        workspace = store.status(created["session_id"])["workspace"]
        action = {
            "action_id": command.command_id,
            "action_digest": command.action_digest(root=catalog.root),
            "owner_id": "project-intelligence",
            "availability": command.availability,
            "mutation_budget": command.risk,
            "arguments": {"workspace": workspace["canonical_root"]},
        }
        ranked = store.append(
            created["session_id"],
            expected_sequence=0,
            frontend=FRONTEND,
            kind="actions-ranked",
            next_actions=[action],
            catalog=catalog,
        )
        prepared = store.prepare_catalog_action(
            created["session_id"],
            expected_sequence=ranked["summary"]["latest_sequence"],
            catalog=catalog,
            expected_catalog_digest=catalog.catalog_digest,
            action_id=command.command_id,
            expected_action_digest=action["action_digest"],
            arguments=action["arguments"],
            workspace_observation=workspace,
            execute=True,
        )
        custody = {
            "owner_id": "workbench-shell",
            "record_id": f"live-console-session:{suffix}",
            "record_kind": "live-console-session-v1",
            "uri": f"workbench://live-console/{suffix}",
            "digest": "sha256:" + "4" * 64,
            "last_verified_state": "allocated",
            "verified_at": "2026-08-21T12:00:00Z",
        }
        bound = store.bind_owner_execution(
            prepared,
            catalog=catalog,
            frontend=FRONTEND,
            owner_record_refs=[custody],
            owner_execution_verifier=_trusted_owner_execution(prepared),
        )
        return store, created, bound, custody

    def test_creation_is_a_durable_journal_event(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            catalog = build_catalog(ROOT)
            store, created = self._create(root, catalog_id=catalog.catalog_digest)

            opened = store.open(created["session_id"])
            self.assertEqual(opened["integrity"]["state"], "verified")
            self.assertEqual(opened["summary"]["latest_sequence"], 0)
            self.assertEqual(opened["events"][0]["kind"], "session-created")
            self.assertEqual(
                validate_work_session_event(opened["events"][0]), opened["events"][0]
            )
            self.assertEqual(
                validate_work_session_summary(opened["summary"]), opened["summary"]
            )

            values = {
                "workbench-work-session-v2.schema.json": opened["session"],
                "workbench-work-session-event-v1.schema.json": opened["events"][0],
                "workbench-work-session-summary-v1.schema.json": opened["summary"],
            }
            for name, value in values.items():
                with self.subTest(schema=name):
                    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
                    Draft202012Validator.check_schema(schema)
                    Draft202012Validator(
                        schema, format_checker=FormatChecker()
                    ).validate(value)

            with self.assertRaisesRegex(WorkSessionError, "frontend-owned"):
                store.create(
                    task={"task_id": "task:forged", "owner_id": "workbench-shell"},
                    workspace=opened["summary"]["workspace"],
                    identities=opened["summary"]["identities"],
                    frontend=FRONTEND,
                    lifecycle="running",
                )

    def test_navigation_append_cannot_invent_owner_or_result_envelopes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            catalog = build_catalog(ROOT)
            store, created = self._create(root, catalog_id=catalog.catalog_digest)
            owner_ref = {
                "owner_id": "crucible",
                "record_id": "job-v2:0123456789abcdef0123456789abcdef",
                "record_kind": "durable-job",
                "uri": "workbench://crucible/jobs/job-v2:0123456789abcdef0123456789abcdef",
                "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "last_verified_state": "running",
                "verified_at": "2026-08-21T12:00:00Z",
            }
            command = catalog.command("doctor.inspect")
            action = {
                "action_id": command.command_id,
                "action_digest": command.action_digest(root=catalog.root),
                "owner_id": "project-intelligence",
                "availability": command.availability,
                "mutation_budget": command.risk,
                "arguments": {
                    "workspace": store.status(created["session_id"])["workspace"][
                        "canonical_root"
                    ]
                },
            }
            with self.assertRaisesRegex(WorkSessionError, "owner-verified custody"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=VSCODE_FRONTEND,
                    kind="invented-running",
                    lifecycle="running",
                )
            with self.assertRaisesRegex(WorkSessionError, "cannot introduce"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=VSCODE_FRONTEND,
                    kind="owner-job-observed",
                    lifecycle="attention",
                    action=action,
                    result_refs=[
                        {"result_id": "result:test", "owner_record_ref": owner_ref}
                    ],
                    owner_record_refs=[owner_ref],
                    catalog=catalog,
                )
            with self.assertRaisesRegex(WorkSessionError, "previously verified"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=VSCODE_FRONTEND,
                    kind="owner-job-observed",
                    lifecycle="attention",
                    action=action,
                    problems=[{
                        "problem_id": "problem:test",
                        "code": "owner.job-running",
                        "severity": "warning",
                        "message": "The owner job has not published a terminal result.",
                        "affected_identity": owner_ref["record_id"],
                        "evidence_refs": [owner_ref],
                        "next_action_id": action["action_id"],
                    }],
                    next_actions=[action],
                    owner_record_refs=[owner_ref],
                    catalog=catalog,
                )
            self.assertEqual(store.status(created["session_id"])["event_count"], 1)

    def test_stale_sequence_fails_without_appending(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            store, created = self._create(Path(temporary))
            store.append(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                kind="first",
            )
            with self.assertRaises(WorkSessionConflictError) as caught:
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=VSCODE_FRONTEND,
                    kind="stale",
                )
            self.assertEqual(caught.exception.code, "work-session.stale-sequence")
            self.assertEqual(store.status(created["session_id"])["event_count"], 2)

    def test_concurrent_frontends_serialize_and_one_stale_cas_loses(self) -> None:
        if os.name != "posix":
            self.skipTest("fork-based adversarial process test")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            context = multiprocessing.get_context("fork")
            gate = context.Event()
            output = context.Queue()
            processes = [
                context.Process(
                    target=_append_from_process,
                    args=(str(root), created["session_id"], gate, output, index),
                )
                for index in range(6)
            ]
            for process in processes:
                process.start()
            gate.set()
            for process in processes:
                process.join(10)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            outcomes = [output.get(timeout=2) for _ in processes]
            self.assertEqual(sum(item[0] == "ok" for item in outcomes), 1)
            self.assertEqual(
                sum(item == ("error", "work-session.stale-sequence") for item in outcomes),
                5,
            )
            opened = store.open(created["session_id"])
            self.assertEqual(opened["summary"]["event_count"], 2)
            self.assertEqual(opened["integrity"]["journal_state"], "verified")

    def test_abrupt_writer_exit_after_event_publish_recovers_exact_event(self) -> None:
        if os.name != "posix":
            self.skipTest("fork-based abrupt-exit test")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            context = multiprocessing.get_context("fork")
            process = context.Process(
                target=_exit_after_event_publish,
                args=(str(root), created["session_id"]),
            )
            process.start()
            process.join(10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 77)

            opened = store.open(created["session_id"])
            self.assertEqual(opened["integrity"]["state"], "summary-regenerated")
            self.assertEqual(opened["summary"]["latest_sequence"], 1)
            self.assertEqual(opened["events"][1]["kind"], "build-started")
            self.assertEqual(opened["summary"]["lifecycle"], "attention")
            preview = store.preview_recovery(created["session_id"])
            self.assertFalse(preview["required"])
            self.assertFalse(preview["automatic"])
            self.assertEqual(store.status(created["session_id"])["event_count"], 2)

    def test_killed_frontend_keeps_owner_child_custody_live_then_reports_dead(self) -> None:
        if os.name != "posix" or not Path("/proc").is_dir():
            self.skipTest("POSIX process-custody probe fixture")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            pid_path = root / "owned-child.pid"
            context = multiprocessing.get_context("fork")
            frontend = context.Process(
                target=_frontend_crash_with_owned_child,
                args=(str(root), created["session_id"], str(pid_path)),
            )
            frontend.start()
            frontend.join(10)
            self.assertFalse(frontend.is_alive())
            self.assertEqual(frontend.exitcode, 78)
            pid = int(pid_path.read_text(encoding="utf-8"))

            def resolve_process(owner_ref):
                observed_pid = int(owner_ref["uri"].removeprefix("process://"))
                state_path = Path(f"/proc/{observed_pid}/stat")
                alive = False
                try:
                    fields = state_path.read_text(encoding="utf-8").split()
                    alive = len(fields) > 2 and fields[2] != "Z"
                except OSError:
                    pass
                return {
                    **owner_ref,
                    "last_verified_state": "running" if alive else "dead",
                    "verified_at": "2026-08-21T12:01:00Z",
                }

            try:
                live = store.preview_recovery(
                    created["session_id"], owner_reference_resolver=resolve_process
                )
                self.assertTrue(live["required"])
                self.assertEqual(live["owner_resolution"], "verified")
                self.assertEqual(
                    live["owner_record_refs"][0]["last_verified_state"], "running"
                )
                self.assertEqual(
                    [row["action_id"] for row in live["safe_actions"]],
                    ["workbench.session.recover"],
                )
                self.assertRegex(
                    live["safe_actions"][0]["action_digest"], r"^sha256:[0-9a-f]{64}$"
                )
                self.assertEqual(os.kill(pid, 0), None)
            finally:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for _ in range(100):
                if resolve_process(live["owner_record_refs"][0])["last_verified_state"] == "dead":
                    break
                time.sleep(0.01)
            dead = store.preview_recovery(
                created["session_id"], owner_reference_resolver=resolve_process
            )
            self.assertEqual(
                dead["owner_record_refs"][0]["last_verified_state"], "dead"
            )
            self.assertFalse(dead["automatic"])

    def test_catalog_preparation_fails_on_digest_or_workspace_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            catalog = build_catalog(ROOT)
            store, created = self._create(root, catalog_id=catalog.catalog_digest)
            command = catalog.command("doctor.inspect")
            digest = command.action_digest(root=catalog.root)
            workspace = store.status(created["session_id"])["workspace"]
            arguments = {"workspace": workspace["canonical_root"]}
            action = {
                "action_id": command.command_id,
                "action_digest": digest,
                "owner_id": "project-intelligence",
                "availability": command.availability,
                "mutation_budget": command.risk,
                "arguments": arguments,
            }
            appended = store.append(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                kind="actions-ranked",
                next_actions=[action],
                catalog=catalog,
            )
            prepared = store.prepare_catalog_action(
                created["session_id"],
                expected_sequence=appended["event"]["sequence"],
                catalog=catalog,
                expected_catalog_digest=catalog.catalog_digest,
                action_id=command.command_id,
                expected_action_digest=digest,
                arguments=arguments,
                workspace_observation=workspace,
                execute=True,
            )
            self.assertFalse(prepared["shell"])
            self.assertEqual(prepared["action"], action)
            self.assertIn("doctor", " ".join(prepared["argv"]))

            with self.assertRaisesRegex(WorkSessionError, "action.*changed"):
                store.prepare_catalog_action(
                    created["session_id"],
                    expected_sequence=appended["event"]["sequence"],
                    catalog=catalog,
                    expected_catalog_digest=catalog.catalog_digest,
                    action_id=command.command_id,
                    expected_action_digest="sha256:" + "0" * 64,
                    arguments=arguments,
                    workspace_observation=workspace,
                    execute=False,
                )
            changed_workspace = {
                **workspace,
                "source_revision": "git:changed",
            }
            with self.assertRaisesRegex(WorkSessionError, "workspace identity"):
                store.prepare_catalog_action(
                    created["session_id"],
                    expected_sequence=appended["event"]["sequence"],
                    catalog=catalog,
                    expected_catalog_digest=catalog.catalog_digest,
                    action_id=command.command_id,
                    expected_action_digest=digest,
                    arguments=arguments,
                    workspace_observation=changed_workspace,
                    execute=False,
                )
            custody = {
                "owner_id": "workbench-shell",
                "record_id": "live-console-session:test",
                "record_kind": "live-console-session-v1",
                "uri": "workbench://live-console/session-test",
                "digest": "sha256:" + "1" * 64,
                "last_verified_state": "running",
                "verified_at": "2026-08-21T12:00:00Z",
            }
            bound = store.bind_owner_execution(
                prepared,
                catalog=catalog,
                frontend=FRONTEND,
                owner_record_refs=[custody],
                owner_execution_verifier=_trusted_owner_execution(prepared),
            )
            self.assertEqual(bound["summary"]["lifecycle"], "running")
            self.assertEqual(bound["summary"]["owner_record_refs"], [custody])

            with self.assertRaisesRegex(WorkSessionError, "cannot prepare another"):
                store.prepare_catalog_action(
                    created["session_id"],
                    expected_sequence=bound["summary"]["latest_sequence"],
                    catalog=catalog,
                    expected_catalog_digest=catalog.catalog_digest,
                    action_id=command.command_id,
                    expected_action_digest=digest,
                    arguments=arguments,
                    workspace_observation=workspace,
                    execute=False,
                )

    def test_owner_binding_loses_the_sequence_race_without_executing(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            catalog = build_catalog(ROOT)
            store, created = self._create(root, catalog_id=catalog.catalog_digest)
            command = catalog.command("doctor.inspect")
            workspace = store.status(created["session_id"])["workspace"]
            action = {
                "action_id": command.command_id,
                "action_digest": command.action_digest(root=catalog.root),
                "owner_id": "project-intelligence",
                "availability": command.availability,
                "mutation_budget": command.risk,
                "arguments": {"workspace": workspace["canonical_root"]},
            }
            ranked = store.append(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                kind="actions-ranked",
                next_actions=[action],
                catalog=catalog,
            )
            prepared = store.prepare_catalog_action(
                created["session_id"],
                expected_sequence=ranked["event"]["sequence"],
                catalog=catalog,
                expected_catalog_digest=catalog.catalog_digest,
                action_id=command.command_id,
                expected_action_digest=action["action_digest"],
                arguments=action["arguments"],
                workspace_observation=workspace,
                execute=True,
            )
            store.append(
                created["session_id"],
                expected_sequence=ranked["event"]["sequence"],
                frontend=VSCODE_FRONTEND,
                kind="frontend-observed",
            )
            with self.assertRaisesRegex(WorkSessionError, "session or workspace changed"):
                store.bind_owner_execution(
                    prepared,
                    catalog=catalog,
                    frontend=FRONTEND,
                    owner_record_refs=[
                        {
                            "owner_id": "crucible",
                            "record_id": "job-v2:preallocated",
                            "record_kind": "durable-job",
                            "uri": "workbench://crucible/jobs/job-v2:preallocated",
                            "digest": "sha256:" + "2" * 64,
                            "last_verified_state": "allocated",
                            "verified_at": "2026-08-21T12:00:00Z",
                        }
                    ],
                    owner_execution_verifier=_trusted_owner_execution(prepared),
                )
            self.assertEqual(store.status(created["session_id"])["lifecycle"], "discovered")

    def test_active_execution_cannot_duplicate_or_partially_complete_custody(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            catalog = build_catalog(ROOT)
            store, created = self._create(root, catalog_id=catalog.catalog_digest)
            command = catalog.command("doctor.inspect")
            workspace = store.status(created["session_id"])["workspace"]
            action = {
                "action_id": command.command_id,
                "action_digest": command.action_digest(root=catalog.root),
                "owner_id": "project-intelligence",
                "availability": command.availability,
                "mutation_budget": command.risk,
                "arguments": {"workspace": workspace["canonical_root"]},
            }
            ranked = store.append(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                kind="actions-ranked",
                next_actions=[action],
                catalog=catalog,
            )
            prepared = store.prepare_catalog_action(
                created["session_id"],
                expected_sequence=ranked["summary"]["latest_sequence"],
                catalog=catalog,
                expected_catalog_digest=catalog.catalog_digest,
                action_id=command.command_id,
                expected_action_digest=action["action_digest"],
                arguments=action["arguments"],
                workspace_observation=workspace,
                execute=True,
            )
            owners = [
                {
                    "owner_id": "workbench-shell",
                    "record_id": f"live-console-session:parallel-{index}",
                    "record_kind": "live-console-session-v1",
                    "uri": f"workbench://live-console/parallel-{index}",
                    "digest": "sha256:" + str(index) * 64,
                    "last_verified_state": "allocated",
                    "verified_at": "2026-08-21T12:00:00Z",
                }
                for index in (1, 2)
            ]
            bound = store.bind_owner_execution(
                prepared,
                catalog=catalog,
                frontend=FRONTEND,
                owner_record_refs=owners,
                owner_execution_verifier=_trusted_owner_execution(prepared),
            )
            with self.assertRaisesRegex(WorkSessionError, "cannot prepare another"):
                store.prepare_catalog_action(
                    created["session_id"],
                    expected_sequence=bound["summary"]["latest_sequence"],
                    catalog=catalog,
                    expected_catalog_digest=catalog.catalog_digest,
                    action_id=command.command_id,
                    expected_action_digest=action["action_digest"],
                    arguments=action["arguments"],
                    workspace_observation=workspace,
                    execute=True,
                )
            terminal_owner = {
                **owners[0],
                "digest": "sha256:" + "3" * 64,
                "last_verified_state": "complete",
                "verified_at": "2026-08-21T12:01:00Z",
            }
            result = {
                "result_id": "result:parallel-one",
                "owner_record_ref": terminal_owner,
            }
            with self.assertRaisesRegex(WorkSessionError, "every retained execution owner"):
                store.bind_owner_result(
                    created["session_id"],
                    expected_sequence=bound["summary"]["latest_sequence"],
                    frontend=FRONTEND,
                    lifecycle="complete",
                    stage_id="doctor.inspect",
                    result_refs=[result],
                    owner_result_verifier=_trusted_owner_result,
                )
            current = store.status(created["session_id"])
            self.assertEqual(current["lifecycle"], "running")
            self.assertEqual(len(current["owner_record_refs"]), 2)
            self.assertEqual(current["result_refs"], [])

    def test_recovery_surfaces_replaced_owner_record_as_stale(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            retained = {
                "owner_id": "crucible",
                "record_id": "job-v2:retained",
                "record_kind": "durable-job",
                "uri": "workbench://crucible/jobs/job-v2:retained",
                "digest": "sha256:" + "3" * 64,
                "last_verified_state": "running",
                "verified_at": "2026-08-21T12:00:00Z",
            }
            store.mark_owner_recoverable(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                reason="owner execution remains unresolved",
                owner_record_refs=[retained],
                owner_reference_verifier=_trusted_owner_reference,
                next_actions=[work_session_recovery_action(created["session_id"])],
            )

            def replaced(reference):
                return {
                    **reference,
                    "digest": "sha256:" + "4" * 64,
                    "last_verified_state": "complete",
                    "verified_at": "2026-08-21T12:01:00Z",
                }

            preview = store.preview_recovery(
                created["session_id"], owner_reference_resolver=replaced
            )
            self.assertEqual(preview["owner_resolution"], "partial")
            self.assertEqual(
                preview["owner_record_refs"][0]["last_verified_state"], "stale"
            )
            self.assertIn("digest changed", preview["owner_resolution_problems"][0]["message"])

            def verified_advance(reference):
                return {
                    "owner_record_ref": {
                        **reference,
                        "digest": "sha256:" + "4" * 64,
                        "last_verified_state": "complete",
                        "verified_at": "2026-08-21T12:01:00Z",
                    },
                    "prior_digest": reference["digest"],
                    "transition": "verified-owner-revision",
                }

            advanced = store.preview_recovery(
                created["session_id"], owner_reference_resolver=verified_advance
            )
            self.assertEqual(advanced["owner_resolution"], "verified")
            self.assertEqual(
                advanced["owner_record_refs"][0]["last_verified_state"], "complete"
            )
            self.assertEqual(advanced["owner_resolution_problems"], [])

    def test_frontend_cannot_invent_a_terminal_owner_outcome(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            with self.assertRaisesRegex(WorkSessionError, "terminal.*owner"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    kind="invented-finish",
                    lifecycle="complete",
                    stage={"stage_id": "build", "state": "complete"},
                )
            forged = dict(store.open(created["session_id"])["events"][0])
            forged["lifecycle"] = "complete"
            forged["stage"] = {"stage_id": "build", "state": "complete"}
            forged["result_refs"] = []
            forged["owner_record_refs"] = []
            forged.pop("event_id")
            forged["event_id"] = "work-session-event:sha256:" + sha256(
                json.dumps(
                    forged,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            with self.assertRaisesRegex(WorkSessionError, "terminal events require"):
                validate_work_session_event(forged)

            owner_refs = [
                {
                    "owner_id": "workbench-shell",
                    "record_id": f"live-console-session:raw-{index}",
                    "record_kind": "live-console-session-v1",
                    "uri": f"workbench://live-console/raw-{index}",
                    "digest": "sha256:" + str(index) * 64,
                    "last_verified_state": "complete",
                    "verified_at": "2026-08-21T12:00:00Z",
                }
                for index in (1, 2)
            ]
            partial = dict(forged)
            partial["owner_record_refs"] = owner_refs
            partial["result_refs"] = [
                {"result_id": "result:only-one", "owner_record_ref": owner_refs[0]}
            ]
            partial.pop("event_id")
            partial["event_id"] = "work-session-event:sha256:" + sha256(
                json.dumps(
                    partial,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            with self.assertRaisesRegex(WorkSessionError, "full owner custody set"):
                validate_work_session_event(partial)

    def test_terminal_result_requires_exact_owner_verification(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            (root / "direct").mkdir()
            direct_store, direct = self._create(root / "direct")
            with self.assertRaisesRegex(WorkSessionError, "prior running"):
                direct_store.bind_owner_result(
                    direct["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    lifecycle="complete",
                    stage_id="build",
                    result_refs=[{
                        "result_id": "result:unrelated",
                        "owner_record_ref": {
                            "owner_id": "workbench-shell",
                            "record_id": "live-console-session:unrelated",
                            "record_kind": "live-console-session-v1",
                            "uri": "workbench://live-console/unrelated",
                            "digest": "sha256:" + "5" * 64,
                            "last_verified_state": "complete",
                            "verified_at": "2026-08-21T12:02:00Z",
                        },
                    }],
                    owner_result_verifier=_trusted_owner_result,
                )

            run_root = root / "run"
            run_root.mkdir()
            store, created, bound, custody = self._bind_running(run_root)
            owner = {
                **custody,
                "digest": "sha256:" + "5" * 64,
                "last_verified_state": "complete",
                "verified_at": "2026-08-21T12:02:00Z",
            }
            completed = store.bind_owner_result(
                created["session_id"],
                expected_sequence=bound["summary"]["latest_sequence"],
                frontend=FRONTEND,
                lifecycle="complete",
                stage_id="build",
                result_refs=[{"result_id": "result:terminal", "owner_record_ref": owner}],
                owner_result_verifier=_trusted_owner_result,
            )
            self.assertEqual(completed["summary"]["lifecycle"], "complete")
            self.assertEqual(completed["summary"]["result_refs"][0]["result_id"], "result:terminal")
            closed = store.close(
                created["session_id"],
                expected_sequence=completed["summary"]["latest_sequence"],
                frontend=VSCODE_FRONTEND,
            )
            self.assertTrue(closed["summary"]["closed"])
            self.assertEqual(closed["summary"]["lifecycle"], "complete")
            self.assertEqual(closed["summary"]["result_refs"], completed["summary"]["result_refs"])
            reopened = store.reopen(
                created["session_id"],
                expected_sequence=closed["summary"]["latest_sequence"],
                frontend=FRONTEND,
            )
            self.assertFalse(reopened["summary"]["closed"])
            self.assertEqual(reopened["summary"]["lifecycle"], "complete")
            preview = store.preview_recovery(
                created["session_id"],
                owner_reference_resolver=lambda reference: {
                    "owner_record_ref": dict(reference),
                    "prior_digest": reference["digest"],
                    "transition": "verified-owner-revision",
                },
            )
            self.assertFalse(preview["required"])
            self.assertEqual(preview["owner_resolution"], "verified")
            self.assertEqual(preview["safe_actions"], [])
            self.assertEqual(preview["owner_resolution_problems"], [])

            substituted_root = root / "substituted"
            substituted_root.mkdir()
            (
                substituted_store,
                substituted,
                substituted_bound,
                substituted_custody,
            ) = self._bind_running(substituted_root, suffix="substituted")
            substituted_owner = {
                **substituted_custody,
                "digest": "sha256:" + "9" * 64,
                "last_verified_state": "complete",
                "verified_at": "2026-08-21T12:02:00Z",
            }
            with self.assertRaisesRegex(WorkSessionError, "exact result identity"):
                second_result = {
                    "result_id": "result:terminal",
                    "owner_record_ref": substituted_owner,
                }
                substituted_store.bind_owner_result(
                    substituted["session_id"],
                    expected_sequence=substituted_bound["summary"]["latest_sequence"],
                    frontend=FRONTEND,
                    lifecycle="complete",
                    stage_id="build",
                    result_refs=[second_result],
                    owner_result_verifier=lambda row: {
                        **row,
                        "result_id": "result:substituted",
                    },
                )

            second_root = root / "second"
            second_root.mkdir()
            second_store, second, second_bound, second_custody = self._bind_running(
                second_root, suffix="forged"
            )
            forged = {**owner, "digest": "not-a-digest"}
            with self.assertRaisesRegex(WorkSessionError, "digest"):
                second_store.bind_owner_result(
                    second["session_id"],
                    expected_sequence=second_bound["summary"]["latest_sequence"],
                    frontend=FRONTEND,
                    lifecycle="complete",
                    stage_id="build",
                    result_refs=[
                        {"result_id": "result:forged", "owner_record_ref": forged}
                    ],
                    owner_result_verifier=_trusted_owner_result,
                )

    def test_nonverified_journal_blocks_append_and_owner_drift_requires_recovery(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            journal = store.base / created["session_id"] / JOURNAL_DIRECTORY_NAME
            (journal / ".interrupted.tmp").write_bytes(b"partial")
            with self.assertRaisesRegex(WorkSessionError, "cannot append"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    kind="must-not-erase-recovery",
                )

            owner = {
                "owner_id": "crucible",
                "record_id": "job-v2:terminal-drift",
                "record_kind": "durable-job-result",
                "uri": "workbench://crucible/jobs/job-v2:terminal-drift/result",
                "digest": "sha256:" + "6" * 64,
                "last_verified_state": "complete",
                "verified_at": "2026-08-21T12:03:00Z",
            }
            clean_root = root / "clean"
            clean_root.mkdir()
            clean_store, clean, clean_bound, custody = self._bind_running(
                clean_root, suffix="drift"
            )
            owner = {
                **custody,
                "digest": "sha256:" + "6" * 64,
                "last_verified_state": "complete",
                "verified_at": "2026-08-21T12:03:00Z",
            }
            clean_store.bind_owner_result(
                clean["session_id"],
                expected_sequence=clean_bound["summary"]["latest_sequence"],
                frontend=FRONTEND,
                lifecycle="complete",
                stage_id="build",
                result_refs=[{"result_id": "result:drift", "owner_record_ref": owner}],
                owner_result_verifier=_trusted_owner_result,
            )

            def replaced(reference):
                return {**reference, "digest": "sha256:" + "7" * 64}

            preview = clean_store.preview_recovery(
                clean["session_id"], owner_reference_resolver=replaced
            )
            self.assertTrue(preview["required"])
            self.assertEqual(preview["owner_resolution"], "partial")
            self.assertEqual(preview["owner_record_refs"][0]["last_verified_state"], "stale")

    def test_move_or_revision_reopen_marks_prior_actions_stale(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            catalog = build_catalog(ROOT)
            store, created = self._create(root, catalog_id=catalog.catalog_digest)
            command = catalog.command("doctor.inspect")
            digest = command.action_digest(root=catalog.root)
            original = store.status(created["session_id"])["workspace"]
            action = {
                "action_id": command.command_id,
                "action_digest": digest,
                "owner_id": "project-intelligence",
                "availability": command.availability,
                "mutation_budget": command.risk,
                "arguments": {"workspace": original["canonical_root"]},
            }
            ranked = store.append(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                kind="actions-ranked",
                next_actions=[action],
                catalog=catalog,
            )
            moved = root / "moved-workspace"
            moved.mkdir()
            reopened = store.reopen(
                created["session_id"],
                expected_sequence=ranked["event"]["sequence"],
                frontend=VSCODE_FRONTEND,
                workspace_observation={
                    "identity_id": original["identity_id"],
                    "canonical_root": str(moved),
                    "source_revision": "git:moved",
                    "dirty_fingerprint": None,
                },
            )
            self.assertEqual(
                reopened["summary"]["next_actions"][0]["availability"], "stale"
            )
            self.assertEqual(store.timeline(created["session_id"])["events"][-1]["kind"], "frontend-reopened")

    def test_truncated_summary_is_regenerated_from_journal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            directory = store.base / created["session_id"]
            (directory / SUMMARY_NAME).write_bytes(b'{"format_version":')

            opened = store.open(created["session_id"])
            self.assertEqual(opened["integrity"]["state"], "summary-regenerated")
            self.assertEqual(opened["summary"], store.status(created["session_id"]))
            json.loads((directory / SUMMARY_NAME).read_text(encoding="utf-8"))

    def test_corrupt_journal_is_isolated_and_cannot_invent_completion(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created, bound, custody = self._bind_running(root, suffix="corrupt")
            owner = {
                **custody,
                "digest": "sha256:" + "8" * 64,
                "last_verified_state": "complete",
                "verified_at": "2026-08-21T12:04:00Z",
            }
            store.bind_owner_result(
                created["session_id"],
                expected_sequence=bound["summary"]["latest_sequence"],
                frontend=FRONTEND,
                lifecycle="complete",
                stage_id="build",
                result_refs=[
                    {"result_id": "result:before-corruption", "owner_record_ref": owner}
                ],
                owner_result_verifier=_trusted_owner_result,
            )
            directory = store.base / created["session_id"]
            terminal_sequence = store.status(created["session_id"])["latest_sequence"]
            event = directory / JOURNAL_DIRECTORY_NAME / f"{terminal_sequence:020d}.json"
            event.write_bytes(b'{"truncated":')

            opened = store.open(created["session_id"])
            self.assertEqual(opened["integrity"]["state"], "corrupt")
            self.assertEqual(opened["summary"]["lifecycle"], "incomplete")
            self.assertEqual(opened["summary"]["latest_sequence"], bound["summary"]["latest_sequence"])
            with self.assertRaisesRegex(WorkSessionError, "cannot append"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    kind="unsafe-append",
                )

    def test_symlinked_storage_journal_and_summary_fail_closed(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            symlinked = root / "symlinked"
            symlinked.mkdir()
            (symlinked / ".workbench").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(WorkSessionError, "symlink"):
                WorkSessionStore(symlinked)

            store, created = self._create(root)
            directory = store.base / created["session_id"]
            summary = directory / SUMMARY_NAME
            summary.unlink()
            summary.symlink_to(outside / "summary.json")
            with self.assertRaisesRegex(WorkSessionError, "symbolic link"):
                store.open(created["session_id"])

            summary.unlink()
            store.regenerate_summary(created["session_id"])
            journal_event = directory / JOURNAL_DIRECTORY_NAME / "00000000000000000000.json"
            copied = outside / "event.json"
            copied.write_bytes(journal_event.read_bytes())
            journal_event.unlink()
            journal_event.symlink_to(copied)
            opened = store.open(created["session_id"])
            self.assertEqual(opened["integrity"]["state"], "corrupt")
            self.assertEqual(opened["summary"]["event_count"], 0)

    def test_recovery_is_explicit_and_close_only_changes_navigation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            owner_ref = {
                "owner_id": "crucible",
                "record_id": "job-v2:0123456789abcdef0123456789abcdef",
                "record_kind": "durable-job",
                "uri": "workbench://crucible/jobs/job-v2:0123456789abcdef0123456789abcdef",
                "digest": "sha256:" + "9" * 64,
                "last_verified_state": "incomplete",
                "verified_at": "2026-08-21T12:00:00Z",
            }
            action = work_session_recovery_action(created["session_id"])
            marked = store.mark_owner_recoverable(
                created["session_id"],
                expected_sequence=0,
                frontend=FRONTEND,
                reason="The frontend exited before the owner published a terminal seal.",
                owner_record_refs=[owner_ref],
                owner_reference_verifier=_trusted_owner_reference,
                next_actions=[action],
            )
            preview = store.preview_recovery(created["session_id"])
            self.assertTrue(preview["required"])
            self.assertFalse(preview["automatic"])
            self.assertEqual(preview["safe_actions"], [action])
            self.assertEqual(store.status(created["session_id"])["event_count"], 2)

            closed = store.close(
                created["session_id"],
                expected_sequence=marked["event"]["sequence"],
                frontend=VSCODE_FRONTEND,
            )
            self.assertTrue(closed["summary"]["closed"])
            self.assertEqual(closed["summary"]["lifecycle"], "recoverable")
            self.assertEqual(closed["summary"]["owner_record_refs"], [owner_ref])
            reopened = store.reopen(
                created["session_id"],
                expected_sequence=closed["event"]["sequence"],
                frontend=FRONTEND,
            )
            self.assertFalse(reopened["summary"]["closed"])

    def test_sensitive_action_arguments_and_copied_results_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            store, created = self._create(Path(temporary))
            with self.assertRaisesRegex(WorkSessionError, "credential-bearing"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    kind="unsafe",
                    action={
                        "action_id": "action:test",
                        "action_digest": "sha256:test",
                        "owner_id": "workbench-shell",
                        "availability": "available",
                        "mutation_budget": "read-only",
                        "arguments": {"api_token": "do-not-retain"},
                    },
                )
            for key in (
                "apiToken",
                "accessToken",
                "clientSecret",
                "authorizationHeader",
            ):
                with self.subTest(secret_key=key), self.assertRaisesRegex(
                    WorkSessionError, "credential-bearing"
                ):
                    store.append(
                        created["session_id"],
                        expected_sequence=0,
                        frontend=FRONTEND,
                        kind="unsafe",
                        action={
                            "action_id": "action:test",
                            "action_digest": "sha256:test",
                            "owner_id": "workbench-shell",
                            "availability": "available",
                            "mutation_budget": "read-only",
                            "arguments": {key: "do-not-retain"},
                        },
                    )
            catalog = build_catalog(ROOT)
            sensitive_command = catalog.command("dev.run")
            with self.assertRaisesRegex(WorkSessionError, "credential-bearing"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    kind="catalog-sensitive",
                    action={
                        "action_id": sensitive_command.command_id,
                        "action_digest": sensitive_command.action_digest(
                            root=catalog.root
                        ),
                        "owner_id": "workbench-shell",
                        "availability": sensitive_command.availability,
                        "mutation_budget": sensitive_command.risk,
                        "arguments": {
                            "run": "candidate:test",
                            "launcher_profile": "Private Launcher Profile Name",
                        },
                    },
                    catalog=catalog,
                )
            serialized = json.dumps(store.open(created["session_id"]), sort_keys=True)
            self.assertNotIn("Private Launcher Profile Name", serialized)
            with self.assertRaisesRegex(WorkSessionError, "unsupported fields"):
                store.append(
                    created["session_id"],
                    expected_sequence=0,
                    frontend=FRONTEND,
                    kind="unsafe",
                    result_refs=[
                        {
                            "result_id": "result:test",
                            "owner_record_ref": {},
                            "result": {"copied": True},
                        }
                    ],
                )

    def test_private_records_and_tampered_header_listing(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            store, created = self._create(root)
            directory = store.base / created["session_id"]
            if os.name == "posix":
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                for path in (
                    directory / SESSION_RECORD_NAME,
                    directory / SUMMARY_NAME,
                    directory / JOURNAL_DIRECTORY_NAME / "00000000000000000000.json",
                ):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            header_path = directory / SESSION_RECORD_NAME
            header = json.loads(header_path.read_text(encoding="utf-8"))
            header["session_id"] = "work-session-v2-ffffffffffffffffffffffffffffffff"
            header_path.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaises(WorkSessionError):
                store.open(created["session_id"])
            listing = store.list()
            self.assertEqual(listing[0]["integrity_state"], "corrupt")
            self.assertEqual(listing[0]["lifecycle"], "incomplete")

    def test_session_records_are_secured_before_atomic_publication(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            store = WorkSessionStore(root)
            operations: list[tuple[str, str]] = []
            actual_secure = work_session.secure_private_path
            actual_private = work_session.private_path
            actual_link = work_session.os.link
            actual_replace = work_session.os.replace

            def observed_secure(path: Path, *, directory: bool) -> Path:
                if not directory:
                    operations.append(("secure", path.name))
                return actual_secure(path, directory=directory)

            def observed_private(path: Path, *, directory: bool) -> bool:
                if not directory:
                    operations.append(("private", path.name))
                return actual_private(path, directory=directory)

            def observed_link(source: Path, target: Path) -> None:
                operations.append(("link", Path(target).name))
                actual_link(source, target)

            def observed_replace(source: Path, target: Path) -> None:
                operations.append(("replace", Path(target).name))
                actual_replace(source, target)

            with (
                patch.object(
                    work_session,
                    "secure_private_path",
                    side_effect=observed_secure,
                ),
                patch.object(
                    work_session,
                    "private_path",
                    side_effect=observed_private,
                ),
                patch.object(work_session.os, "link", side_effect=observed_link),
                patch.object(
                    work_session.os,
                    "replace",
                    side_effect=observed_replace,
                ),
            ):
                created = store.create(
                    task={"task_id": "task:secure-publish", "owner_id": "workbench-shell"},
                    workspace={
                        "identity_id": "workspace:secure-publish",
                        "canonical_root": str(workspace),
                        "source_revision": "git:test",
                        "dirty_fingerprint": None,
                    },
                    identities={
                        "core_id": "core:test",
                        "catalog_id": "catalog:test",
                        "host_adapter_id": "host:test",
                        "platform_profile_id": "platform:test",
                        "pack_profile_id": None,
                    },
                    frontend=FRONTEND,
                )

            directory = store.base / created["session_id"]
            targets = (
                (directory / SESSION_RECORD_NAME, "link"),
                (
                    directory
                    / JOURNAL_DIRECTORY_NAME
                    / "00000000000000000000.json",
                    "link",
                ),
                (directory / SUMMARY_NAME, "replace"),
            )
            for target, publication in targets:
                with self.subTest(target=target.name):
                    prefix = f".{target.name}."
                    secure_index = next(
                        index
                        for index, operation in enumerate(operations)
                        if operation[0] == "secure" and operation[1].startswith(prefix)
                    )
                    temporary_private_index = next(
                        index
                        for index, operation in enumerate(operations)
                        if index > secure_index
                        and operation[0] == "private"
                        and operation[1].startswith(prefix)
                    )
                    publish_index = operations.index((publication, target.name))
                    published_private_index = next(
                        index
                        for index, operation in enumerate(operations)
                        if index > publish_index
                        and operation == ("private", target.name)
                    )
                    self.assertLess(secure_index, temporary_private_index)
                    self.assertLess(temporary_private_index, publish_index)
                    self.assertLess(publish_index, published_private_index)

    def test_record_security_failures_do_not_publish_or_acknowledge(self) -> None:
        failures = (
            SESSION_RECORD_NAME,
            "00000000000000000000.json",
            SUMMARY_NAME,
        )
        for failed_name in failures:
            with self.subTest(record=failed_name), tempfile.TemporaryDirectory(
                dir="/tmp"
            ) as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                store = WorkSessionStore(root)
                actual_secure = work_session.secure_private_path

                def fail_selected(path: Path, *, directory: bool) -> Path:
                    if not directory and path.name.startswith(f".{failed_name}."):
                        raise HostFilesystemError("cannot apply private ACL")
                    return actual_secure(path, directory=directory)

                with (
                    patch.object(
                        work_session,
                        "secure_private_path",
                        side_effect=fail_selected,
                    ),
                    self.assertRaises(WorkSessionError) as raised,
                ):
                    store.create(
                        task={
                            "task_id": "task:secure-failure",
                            "owner_id": "workbench-shell",
                        },
                        workspace={
                            "identity_id": "workspace:secure-failure",
                            "canonical_root": str(workspace),
                            "source_revision": "git:test",
                            "dirty_fingerprint": None,
                        },
                        identities={
                            "core_id": "core:test",
                            "catalog_id": "catalog:test",
                            "host_adapter_id": "host:test",
                            "platform_profile_id": "platform:test",
                            "pack_profile_id": None,
                        },
                        frontend=FRONTEND,
                    )

                self.assertEqual(raised.exception.code, "work-session.unsafe-path")
                self.assertIsInstance(raised.exception.__cause__, HostFilesystemError)
                directories = [
                    path
                    for path in store.base.iterdir()
                    if path.name != work_session.LOCK_DIRECTORY_NAME
                ]
                self.assertEqual(len(directories), 1)
                directory = directories[0]
                targets = {
                    SESSION_RECORD_NAME: directory / SESSION_RECORD_NAME,
                    "00000000000000000000.json": (
                        directory
                        / JOURNAL_DIRECTORY_NAME
                        / "00000000000000000000.json"
                    ),
                    SUMMARY_NAME: directory / SUMMARY_NAME,
                }
                self.assertFalse(targets[failed_name].exists())
                self.assertEqual(
                    list(directory.rglob(f".{failed_name}.*")),
                    [],
                )

    def test_new_session_lock_is_secured_before_private_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            store, created = self._create(Path(temporary))
            operations: list[str] = []
            actual_secure = work_session.secure_private_path
            actual_private = work_session.private_path

            def observed_secure(path: Path, *, directory: bool) -> Path:
                if path.parent == store.locks:
                    operations.append("secure")
                return actual_secure(path, directory=directory)

            def observed_private(path: Path, *, directory: bool) -> bool:
                if path.parent == store.locks:
                    operations.append("private")
                return actual_private(path, directory=directory)

            with (
                patch.object(
                    work_session,
                    "secure_private_path",
                    side_effect=observed_secure,
                ),
                patch.object(
                    work_session,
                    "private_path",
                    side_effect=observed_private,
                ),
            ):
                opened = store.status(created["session_id"])

            self.assertEqual(opened["session_id"], created["session_id"])
            self.assertEqual(operations, ["secure", "private"])

    def test_session_lock_security_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            store, created = self._create(Path(temporary))
            with patch.object(
                work_session,
                "secure_private_path",
                side_effect=HostFilesystemError("cannot apply private ACL"),
            ):
                with self.assertRaises(WorkSessionError) as raised:
                    store.status(created["session_id"])

            self.assertEqual(raised.exception.code, "work-session.unsafe-path")
            self.assertIsInstance(raised.exception.__cause__, HostFilesystemError)

    def test_existing_unsafe_session_lock_is_rejected_without_repair(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            store, created = self._create(Path(temporary))
            store.status(created["session_id"])
            actual_private = work_session.private_path

            def reject_lock(path: Path, *, directory: bool) -> bool:
                if path.parent == store.locks:
                    return False
                return actual_private(path, directory=directory)

            with (
                patch.object(work_session, "secure_private_path") as secure,
                patch.object(
                    work_session,
                    "private_path",
                    side_effect=reject_lock,
                ),
                self.assertRaises(WorkSessionError) as raised,
            ):
                store.status(created["session_id"])

            self.assertEqual(raised.exception.code, "work-session.unsafe-path")
            secure.assert_not_called()

    def test_widened_session_permissions_never_validate_as_verified(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX owner-private mode probe")
        targets = ("directory", "header", "summary", "journal", "event")
        for target in targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir="/tmp") as temporary:
                store, created = self._create(Path(temporary))
                directory = store.base / created["session_id"]
                paths = {
                    "directory": directory,
                    "header": directory / SESSION_RECORD_NAME,
                    "summary": directory / SUMMARY_NAME,
                    "journal": directory / JOURNAL_DIRECTORY_NAME,
                    "event": directory / JOURNAL_DIRECTORY_NAME / "00000000000000000000.json",
                }
                paths[target].chmod(0o755 if paths[target].is_dir() else 0o644)
                try:
                    opened = store.open(created["session_id"])
                except WorkSessionError as exc:
                    self.assertIn(exc.code, {
                        "work-session.unsafe-path",
                        "work-session.invalid-record",
                    })
                else:
                    self.assertNotEqual(opened["integrity"]["state"], "verified")
                    self.assertNotEqual(opened["summary"]["integrity_state"], "verified")


if __name__ == "__main__":
    unittest.main()
