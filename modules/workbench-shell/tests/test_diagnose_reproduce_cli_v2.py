from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_shell.catalog import build_catalog, redact_argv
from workbench_shell.diagnose_reproduce_cli import diagnose_main
from workbench_shell.diagnose_reproduce import create_reproduction_capsule
from workbench_api.events import EventNormalizer, RawLocator
from workbench_core.sessions import (
    RetainedSession,
    live_console_execution_reference,
    live_console_owner_reference,
)
from workbench_shell.work_session import WorkSessionStore


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = {
    "frontend_id": "workbench-cli-test",
    "kind": "cli",
    "version": "test",
}


class DiagnoseReproduceCliV2Tests(unittest.TestCase):
    def test_public_diagnose_help_discovers_capsule_routes(self) -> None:
        public = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "workbench.py"),
                "diagnose",
                "--help",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(public.returncode, 0, public.stderr)
        self.assertIn(
            "workbench diagnose reproduce {create,inspect,verify,run}",
            public.stdout,
        )

    def _failed_live_console(self, state: Path, *, session_id: str) -> str:
        retained = RetainedSession(
            root=state,
            command_id="cleanroom-dev-server",
            argv=["workbench", "dev", "fixture", "run", "--side", "server"],
            cwd=state,
            intent="inspect",
            session_id=session_id,
        )
        raw = b"Client-only class loaded on the dedicated server\n"
        locator = retained.write_raw("stderr", raw)
        event = EventNormalizer(root=state).normalize(
            raw.decode("utf-8").rstrip("\n"),
            source="cleanroom-dev-server",
            stream="stderr",
            raw_locator=RawLocator(
                artifact=locator.path,
                byte_start=locator.byte_start,
                byte_end=locator.byte_end,
                line=1,
                boundary="lf",
            ),
        ).as_dict()
        event["severity"] = "fatal"
        event["outcome_failure"] = True
        retained.record_event(event)
        retained.finish(
            state="failed",
            process_exit_code=None,
            effective_exit_code=1,
            outcome="observed-required-failure",
        )
        return retained.session_id

    def _failed_work_session(self, state: Path) -> str:
        catalog = build_catalog(ROOT)
        workspace = state / "workspace"
        workspace.mkdir()
        store = WorkSessionStore(state)
        created = store.create(
            task={"task_id": "task:diagnose-cli", "owner_id": "workbench-shell"},
            workspace={
                "identity_id": "workspace:diagnose-cli",
                "canonical_root": str(workspace),
                "source_revision": "fixture:diagnose-cli",
                "dirty_fingerprint": None,
            },
            identities={
                "core_id": "core:test",
                "catalog_id": catalog.catalog_digest,
                "host_adapter_id": "host:test",
                "platform_profile_id": "platform:test",
                "pack_profile_id": None,
            },
            frontend=FRONTEND,
        )
        session_id = created["session_id"]
        command = catalog.command("doctor.inspect")
        action = {
            "action_id": command.command_id,
            "action_digest": command.action_digest(root=catalog.root),
            "owner_id": "project-intelligence",
            "availability": command.availability,
            "mutation_budget": command.risk,
            "arguments": {"workspace": str(workspace)},
        }
        ranked = store.append(
            session_id,
            expected_sequence=0,
            frontend=FRONTEND,
            kind="actions-ranked",
            next_actions=[action],
            catalog=catalog,
        )
        prepared = store.prepare_catalog_action(
            session_id,
            expected_sequence=ranked["summary"]["latest_sequence"],
            catalog=catalog,
            expected_catalog_digest=catalog.catalog_digest,
            action_id=command.command_id,
            expected_action_digest=action["action_digest"],
            arguments=action["arguments"],
            workspace_observation=ranked["summary"]["workspace"],
            execute=True,
        )
        retained = RetainedSession(
            root=state,
            command_id=command.command_id,
            argv=redact_argv(prepared["argv"], command.fields),
            cwd=workspace,
            intent=prepared["intent"],
            label="diagnose CLI fixture",
        )
        historical = live_console_owner_reference(state, retained.session_id)
        store.bind_owner_execution(
            prepared,
            catalog=catalog,
            frontend=FRONTEND,
            owner_record_refs=[historical],
            owner_execution_verifier=lambda row: live_console_execution_reference(
                state, row
            ),
        )
        raw = b"Mixin target example.Target was not found\n"
        locator = retained.write_raw("stderr", raw)
        event = EventNormalizer(root=workspace).normalize(
            raw.decode("utf-8").rstrip("\n"),
            source=command.command_id,
            stream="stderr",
            raw_locator=RawLocator(
                artifact=locator.path,
                byte_start=locator.byte_start,
                byte_end=locator.byte_end,
                line=1,
                boundary="lf",
            ),
        ).as_dict()
        event["severity"] = "fatal"
        event["outcome_failure"] = True
        retained.record_event(event)
        retained.finish(
            state="failed",
            process_exit_code=None,
            effective_exit_code=1,
            outcome="observed-required-failure",
        )
        return session_id

    def test_latest_and_capsule_commands_use_the_retained_work_session(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary)
            session_id = self._failed_work_session(state)
            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    ["latest", "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                0,
                error.getvalue(),
            )
            diagnosis = json.loads(output.getvalue())
            self.assertEqual(diagnosis["format"], "workbench-diagnosis-v1")
            self.assertEqual(diagnosis["work_session_id"], session_id)
            self.assertEqual(
                diagnosis["observed_failures"][0]["message"],
                "Mixin target example.Target was not found",
            )
            public = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "workbench.py"),
                    "diagnose",
                    "latest",
                    "--state-root",
                    str(state),
                    "--json",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(public.returncode, 0, public.stderr)
            self.assertEqual(json.loads(public.stdout)["diagnosis_id"], diagnosis["diagnosis_id"])

            capsule = state / "failure.wb-repro"
            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    [
                        "reproduce",
                        "create",
                        "latest",
                        "--state-root",
                        str(state),
                        "--output",
                        str(capsule),
                        "--action-id",
                        "doctor.inspect",
                        "--arguments-json",
                        json.dumps({"workspace": str(state / "workspace")}),
                        "--mutation",
                        "read-only",
                        "--approve-privacy",
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                0,
                error.getvalue(),
            )
            created = json.loads(output.getvalue())
            self.assertTrue(capsule.is_file())
            self.assertTrue(created["capsule_id"].startswith(
                "workbench-reproduction-capsule:sha256:"
            ))

            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    ["reproduce", "inspect", str(capsule), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                0,
                error.getvalue(),
            )
            inspected = json.loads(output.getvalue())
            self.assertEqual(inspected["capsule_id"], created["capsule_id"])

    def test_missing_work_session_and_unapproved_review_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary)
            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    ["latest", "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                2,
            )
            self.assertIn(
                "no Work Sessions or retained live-console sessions exist",
                error.getvalue(),
            )

    def test_latest_falls_back_to_exact_d01_live_console_owner(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary)
            owner_id = self._failed_live_console(
                state,
                session_id="d01-failed-server-owner",
            )
            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    ["latest", "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                0,
                error.getvalue(),
            )
            diagnosis = json.loads(output.getvalue())
            self.assertIsNone(diagnosis["work_session_id"])
            self.assertEqual(diagnosis["target_owner_ref"]["record_id"], owner_id)
            self.assertEqual(
                diagnosis["observed_failures"][0]["message"],
                "Client-only class loaded on the dedicated server",
            )

            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    [f"live:{owner_id}", "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                0,
                error.getvalue(),
            )
            self.assertEqual(
                json.loads(output.getvalue())["target_owner_ref"]["record_id"],
                owner_id,
            )

    def test_d01_owner_stage_is_joined_without_reclassifying_raw_output(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary)
            (state / "dev-loop").mkdir()
            owner_id = self._failed_live_console(
                state,
                session_id="d01-semantic-server-owner",
            )
            receipt_digest = "sha256:" + "d" * 64
            joined = {
                "receipt": {
                    "kind": "workbench-cleanroom-dev-loop-receipt",
                    "receipt_id": (
                        "workbench-cleanroom-dev-loop-receipt:" + receipt_digest
                    ),
                    "target": {
                        "receipt_uri": (state / "dev-loop/run/receipt.json").as_uri(),
                    },
                },
                "stage": "server",
                "stage_result": {
                    "state": "failed",
                    "problem": "required server readiness markers were not observed",
                    "effective_exit_code": 0,
                    "required_markers": [
                        "dedicated-server-ready",
                        "common-registry-ready",
                    ],
                    "observed_markers": [],
                    "artifact_sha256": "sha256:" + "e" * 64,
                    "cleanup": {"contained": True},
                },
            }
            recovery = {
                "next_action": {
                    "action_id": "dev.fixture-run",
                    "source_plan_id": (
                        "workbench-cleanroom-dev-loop-plan:sha256:" + "f" * 64
                    ),
                    "reconstruction_inputs": {
                        "gradle_cmd": "/owner/gradle",
                        "java_home": "/owner/jdk",
                        "state_root": "/owner/state",
                        "sides": ["server"],
                        "debug": False,
                    },
                }
            }
            output, error = StringIO(), StringIO()
            with patch(
                "workbench_shell.diagnose_reproduce_cli.find_cleanroom_dev_loop_stage",
                return_value=joined,
            ), patch(
                "workbench_shell.diagnose_reproduce_cli.recover_cleanroom_dev_loop",
                return_value=recovery,
            ):
                self.assertEqual(
                    diagnose_main(
                        [f"live:{owner_id}", "--state-root", str(state), "--json"],
                        root=ROOT,
                        output=output,
                        error=error,
                    ),
                    0,
                    error.getvalue(),
                )
            diagnosis = json.loads(output.getvalue())
            self.assertEqual(
                diagnosis["classifications"][0]["owner_record_digest"],
                receipt_digest,
            )
            self.assertEqual(
                diagnosis["classifications"][0]["stage"],
                "server",
            )
            self.assertEqual(
                diagnosis["next_experiments"][1]["arguments"]["sides"],
                ["server"],
            )

    def test_capsule_run_uses_installed_dev_fixture_owner(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary)
            owner_id = self._failed_live_console(
                state,
                session_id="d01-replay-source-owner",
            )
            output, error = StringIO(), StringIO()
            self.assertEqual(
                diagnose_main(
                    [f"live:{owner_id}", "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                0,
                error.getvalue(),
            )
            diagnosis = json.loads(output.getvalue())
            capsule = state / "dev-fixture.wb-repro"
            create_reproduction_capsule(
                diagnosis,
                capsule,
                replay_action={
                    "action_id": "dev.fixture-run",
                    "arguments": {
                        "source_plan_id": (
                            "workbench-cleanroom-dev-loop-plan:sha256:" + "a" * 64
                        ),
                        "sides": ["server"],
                        "debug": False,
                    },
                    "mutation": "isolated-target-only",
                },
                privacy_review={
                    "approved": True,
                    "excluded": [
                        "credentials",
                        "personal-worlds",
                        "protected-binaries",
                    ],
                },
            )
            plan = {"plan_id": "fresh-plan"}
            result = {
                "outcome": "failed",
                "receipt": {
                    "stages": {
                        "build": {"state": "passed"},
                        "server": {
                            "state": "failed",
                            "owner_ref": {"record_id": "replayed-owner"},
                        },
                    }
                },
            }
            output, error = StringIO(), StringIO()
            with patch(
                "workbench_shell.diagnose_reproduce_cli.plan_cleanroom_dev_loop",
                return_value=plan,
            ), patch(
                "workbench_shell.diagnose_reproduce_cli.execute_cleanroom_dev_loop",
                return_value=result,
            ), patch(
                "workbench_shell.diagnose_reproduce_cli._cleanroom_diagnosis_context",
                return_value=([], []),
            ), patch(
                "workbench_shell.diagnose_reproduce_cli.diagnose_live_console",
                return_value={"fingerprint": diagnosis["fingerprint"]},
            ):
                self.assertEqual(
                    diagnose_main(
                        [
                            "reproduce",
                            "run",
                            str(capsule),
                            "--state-root",
                            str(state / "replay"),
                            "--gradle-cmd",
                            str(state / "gradle"),
                            "--java-home",
                            str(state / "jdk"),
                            "--json",
                        ],
                        root=ROOT,
                        output=output,
                        error=error,
                    ),
                    0,
                    error.getvalue(),
                )
            replay = json.loads(output.getvalue())
            self.assertEqual(replay["outcome"], "matching-failure")
            self.assertTrue(replay["matched"])


if __name__ == "__main__":
    unittest.main()
