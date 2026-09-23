"""Failure-first public journey tests for the Workbench product spine."""

from __future__ import annotations

from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/atlas/src",
    ROOT / "modules/blueprints/src",
    ROOT / "modules/crucible/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_core.development import enable_source_checkout  # noqa: E402
enable_source_checkout(ROOT)

from workbench_shell.product_capability_catalog import (  # noqa: E402
    load_product_capability_catalog,
)
from workbench_shell.catalog import build_catalog, redact_argv  # noqa: E402
from workbench_shell.product_spine_cli import (  # noqa: E402
    _owner_resolver,
    _workspace_observation,
    adopt_main,
    build_home_for_cli,
    home_main,
    reopen_main,
    session_main,
)
from workbench_core.sessions import (  # noqa: E402
    RetainedSession,
    list_sessions,
    live_console_execution_reference,
    live_console_owner_reference,
)
from workbench_shell.work_session import (  # noqa: E402
    WorkSessionConflictError,
    WorkSessionStore,
)


def start_bound_fixture_process(
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


ROUTER = ROOT / "tools/workbench.py"
CLEANROOM_FIXTURE = (
    ROOT / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
)


def _tree(root: Path) -> list[tuple[str, int, str]]:
    return [
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


class ProductSpineCliV2Tests(unittest.TestCase):
    def test_json_home_keeps_optional_catalog_diagnostics_off_stderr(self) -> None:
        home = {
            "format": "workbench-workspace-home-v2",
            "workspace": {"workspace_id": "workspace:sha256:" + "1" * 64},
        }
        output = StringIO()
        error = StringIO()
        with patch(
            "workbench_shell.product_spine_cli.build_home_for_cli",
            return_value=(home, ["capability catalog is unavailable"]),
        ):
            result = home_main(
                [str(ROOT), "--json"],
                root=ROOT,
                output=output,
                error=error,
            )
        self.assertEqual(0, result)
        self.assertEqual("", error.getvalue())
        self.assertEqual(home, json.loads(output.getvalue()))

    def test_workspace_observation_does_not_load_optional_capability_catalog(self) -> None:
        home = {
            "workspace": {
                "workspace_id": "workspace:sha256:" + "1" * 64,
                "root": str(ROOT),
                "source_revision": "git:test",
                "dirty_fingerprint": "sha256:" + "2" * 64,
            }
        }
        with (
            patch(
                "workbench_shell.product_spine_cli.load_product_capability_owner_port",
                side_effect=AssertionError("optional catalog must not load"),
            ),
            patch(
                "workbench_shell.product_spine_cli.build_workspace_home_v2",
                return_value=home,
            ) as build_home,
        ):
            observed = _workspace_observation(ROOT, ROOT)
        build_home.assert_called_once_with(ROOT, ROOT)
        self.assertEqual("workspace:sha256:" + "1" * 64, observed["identity_id"])

    def test_unadopted_home_is_built_once(self) -> None:
        home = {
            "workspace": {
                "workspace_id": "workspace:sha256:" + "1" * 64,
            }
        }
        with (
            patch(
                "workbench_shell.product_spine_cli._capability_catalog_port",
                return_value=(None, None),
            ),
            patch(
                "workbench_shell.product_spine_cli.build_workspace_home_v2",
                return_value=home,
            ) as build_home,
            patch(
                "workbench_shell.product_spine_cli.workspace_home_adoption_exists",
                return_value=False,
            ),
        ):
            result, diagnostics = build_home_for_cli(
                ROOT,
                ROOT,
                state_base=ROOT,
            )
        self.assertIs(result, home)
        self.assertEqual([], diagnostics)
        self.assertEqual(1, build_home.call_count)

    def test_owner_resolver_pages_to_exact_execution_binding(self) -> None:
        owner = {
            "owner_id": "workbench-shell",
            "record_id": "owner-page-session",
            "record_kind": "workbench-live-console-session-v1",
            "uri": "file:///tmp/owner-page-session/session-v1.json",
            "digest": "sha256:" + "1" * 64,
            "last_verified_state": "allocated",
            "verified_at": "2026-08-21T00:00:00Z",
        }
        execution = {
            "owner_record_ref": dict(owner),
            "command": {
                "command_id": "doctor.inspect",
                "argv": ["workbench", "doctor"],
                "cwd": "/tmp/workspace",
                "intent": "execute",
                "shell": False,
            },
        }
        integrity = {
            "state": "verified",
            "journal_state": "verified",
            "summary_state": "verified",
            "problems": [],
        }
        store = Mock()
        store.timeline.side_effect = [
            {
                "events": [],
                "has_more": True,
                "next_sequence": 4095,
                "integrity": integrity,
            },
            {
                "events": [
                    {
                        "kind": "owner-execution-bound",
                        "action": {"action_id": "doctor.inspect"},
                        "owner_record_refs": [owner],
                    }
                ],
                "has_more": False,
                "next_sequence": 4096,
                "integrity": integrity,
            },
        ]
        with patch(
            "workbench_shell.product_spine_cli.live_console_execution_reference",
            return_value=execution,
        ):
            resolved = _owner_resolver(
                Path("/tmp/state"),
                owner,
                root=ROOT,
                store=store,
                selector="work-session",
            )
        self.assertEqual(resolved["transition"], "verified-owner-revision")
        self.assertEqual(
            [call.kwargs["after_sequence"] for call in store.timeline.call_args_list],
            [-1, 4095],
        )

    def _physical_cleanroom_tools(self) -> tuple[str, str]:
        gradle = os.environ.get("WORKBENCH_CLEANROOM_FIXTURE_GRADLEW")
        java_home = os.environ.get("WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME")
        if not gradle or not java_home:
            self.skipTest(
                "set the Cleanroom fixture Gradle and Java 25 environment variables"
            )
        return gradle, java_home

    def _root(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROUTER), *arguments],
            cwd=cwd or ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def _adopt(self, base: Path) -> tuple[Path, Path, dict]:
        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
        state = base / "state"
        output, error = StringIO(), StringIO()
        code = adopt_main(
            [str(workspace), "--state-root", str(state), "--json"],
            root=ROOT,
            output=output,
            error=error,
        )
        self.assertEqual(code, 0, error.getvalue())
        return workspace, state, json.loads(output.getvalue())

    def test_root_help_and_capabilities_consume_the_live_catalog(self) -> None:
        catalog = load_product_capability_catalog(ROOT)
        help_result = self._root("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in ("capabilities", "session", "adopt", "reopen"):
            self.assertIn(command, help_result.stdout)
        self.assertIn("Workbench Core:", help_result.stdout)
        self.assertNotIn(catalog["catalog_id"], help_result.stdout)

        capabilities = self._root("capabilities", "--json")
        self.assertEqual(capabilities.returncode, 0, capabilities.stderr)
        value = json.loads(capabilities.stdout)
        self.assertEqual(value["catalog_id"], catalog["catalog_id"])
        self.assertEqual("product-capabilities", value["view"])
        self.assertEqual(value["count"], len(catalog["capabilities"]))
        for row in value["capabilities"]:
            self.assertIn(row["availability"], {"available", "experimental", "unavailable"})
            self.assertIn(row["handler"]["kind"], {"process", "document"})
            self.assertNotIn("owner_state", row)
            self.assertNotIn("evidence_level", row)
            self.assertNotIn("placement", row)

    def test_open_defaults_to_v2_without_mutating_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            (workspace / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
            before = _tree(workspace)

            opened = self._root("open", str(workspace), "--json", cwd=workspace)
            self.assertEqual(opened.returncode, 0, opened.stderr)
            home = json.loads(opened.stdout)
            self.assertEqual(home["format"], "workbench-workspace-home-v2")
            self.assertLessEqual(len(home["jobs"]), 5)
            self.assertEqual(home["new_project"]["state"], "available")
            self.assertEqual(
                home["new_project"]["admitted_kinds"],
                ["workbench-new-project-kind:cleanroom-mod"],
            )
            self.assertEqual(home["capability_catalog"]["catalog_id"],
                             load_product_capability_catalog(ROOT)["catalog_id"])
            self.assertEqual(before, _tree(workspace))

            help_result = self._root("open", "--help", cwd=workspace)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertNotIn("--format", help_result.stdout)

    def test_adopt_reopen_and_session_routes_share_exact_identities(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            (workspace / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
            state = base / "state"
            before = _tree(workspace)

            adopted_out, adopted_err = StringIO(), StringIO()
            adopted_code = adopt_main(
                [str(workspace), "--state-root", str(state), "--json"],
                root=ROOT,
                output=adopted_out,
                error=adopted_err,
            )
            self.assertEqual(adopted_code, 0, adopted_err.getvalue())
            adopted = json.loads(adopted_out.getvalue())
            session_id = adopted["session"]["session_id"]
            binding_id = adopted["adoption"]["binding_id"]
            catalog_id = adopted["capability_catalog"]["catalog_id"]
            self.assertEqual(adopted["adoption"]["session_id"], session_id)
            self.assertEqual(before, _tree(workspace))

            status_out, status_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    ["status", session_id, "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=status_out,
                    error=status_err,
                ),
                0,
                status_err.getvalue(),
            )
            status = json.loads(status_out.getvalue())
            self.assertEqual(status["session_id"], session_id)
            self.assertTrue(
                status["identities"]["host_adapter_id"].startswith(
                    "workbench-host-adapter-conformance-v3:sha256:"
                )
            )

            reopen_out, reopen_err = StringIO(), StringIO()
            self.assertEqual(
                reopen_main(
                    [binding_id, "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=reopen_out,
                    error=reopen_err,
                ),
                0,
                reopen_err.getvalue(),
            )
            reopened = json.loads(reopen_out.getvalue())
            # Home snapshots are content-addressed and the reopen operation is
            # itself retained, so their snapshot IDs must differ.  The shared
            # product identities must remain exact across that transition.
            self.assertNotEqual(reopened["home_id"], adopted["home_id"])
            self.assertEqual(reopened["workspace"], adopted["workspace"])
            self.assertEqual(reopened["session"]["session_id"], session_id)
            self.assertEqual(reopened["capability_catalog"]["catalog_id"], catalog_id)
            self.assertEqual(reopened["adoption"]["binding_id"], binding_id)
            self.assertEqual(before, _tree(workspace))

            duplicate_out, duplicate_err = StringIO(), StringIO()
            self.assertEqual(
                adopt_main(
                    [str(workspace), "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=duplicate_out,
                    error=duplicate_err,
                ),
                2,
            )
            self.assertIn("already adopted", duplicate_err.getvalue())
            self.assertEqual(len(WorkSessionStore(state).list()), 1)

            timeline_out, timeline_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    ["timeline", session_id, "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=timeline_out,
                    error=timeline_err,
                ),
                0,
                timeline_err.getvalue(),
            )
            timeline = json.loads(timeline_out.getvalue())
            self.assertEqual(
                [row["sequence"] for row in timeline["events"]], [0, 1, 2]
            )
            self.assertEqual(timeline["events"][-1]["kind"], "frontend-reopened")
            self.assertEqual(
                {row["frontend"]["kind"] for row in timeline["events"]}, {"cli"}
            )

    def test_resume_can_bind_a_caller_observed_sequence_and_reject_stale(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace, state, adopted = self._adopt(base)
            session_id = adopted["session"]["session_id"]
            before = WorkSessionStore(state).status(session_id)
            arguments = [
                "resume",
                session_id,
                "--workspace",
                str(workspace),
                "--expected-sequence",
                str(before["latest_sequence"]),
                "--state-root",
                str(state),
                "--json",
            ]

            winner_out, winner_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    arguments,
                    root=ROOT,
                    output=winner_out,
                    error=winner_err,
                ),
                0,
                winner_err.getvalue(),
            )
            winner = json.loads(winner_out.getvalue())
            self.assertEqual(
                winner["latest_sequence"], before["latest_sequence"] + 1
            )

            stale_out, stale_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    arguments,
                    root=ROOT,
                    output=stale_out,
                    error=stale_err,
                ),
                2,
            )
            self.assertIn("stale", stale_err.getvalue().lower())
            self.assertEqual(
                WorkSessionStore(state).status(session_id)["latest_sequence"],
                winner["latest_sequence"],
            )

    def test_session_run_rejects_a_stale_action_digest_before_owner_allocation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            _workspace, state, adopted = self._adopt(base)
            session_id = adopted["session"]["session_id"]
            before = WorkSessionStore(state).status(session_id)
            output, error = StringIO(), StringIO()

            with patch(
                "workbench_shell.product_spine_cli.RetainedSession"
            ) as retained:
                code = session_main(
                    [
                        "run",
                        session_id,
                        "doctor.inspect",
                        "--execute",
                        "--expected-action-digest",
                        "sha256:" + "0" * 64,
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                )

            self.assertEqual(code, 2)
            self.assertIn("stale", error.getvalue().lower())
            retained.assert_not_called()
            self.assertEqual(WorkSessionStore(state).status(session_id), before)

    def test_interrupted_adoption_retains_catalog_retry_without_owner_custody(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            (workspace / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
            state = base / "state"
            output, error = StringIO(), StringIO()
            with patch(
                "workbench_shell.product_spine_cli.adopt_workspace_home_v2",
                side_effect=OSError("injected adoption publication failure"),
            ):
                code = adopt_main(
                    [str(workspace), "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=output,
                    error=error,
                )
            self.assertEqual(code, 2)
            self.assertIn("injected adoption publication failure", error.getvalue())

            store = WorkSessionStore(state)
            listing = store.list()
            self.assertEqual(len(listing), 1)
            status = store.status(listing[0]["session_id"])
            self.assertEqual(status["lifecycle"], "attention")
            self.assertEqual(status["owner_record_refs"], [])
            self.assertEqual(status["result_refs"], [])
            self.assertEqual(status["recovery"]["state"], "required")
            self.assertEqual(status["recovery"]["owner_record_refs"], [])
            self.assertEqual(
                status["recovery"]["safe_action_ids"], ["workspace.adopt"]
            )
            self.assertEqual(
                [row["action_id"] for row in status["next_actions"]],
                ["workspace.adopt"],
            )
            catalog = build_catalog(ROOT)
            prepared = store.prepare_catalog_action(
                status["session_id"],
                expected_sequence=status["latest_sequence"],
                catalog=catalog,
                expected_catalog_digest=status["identities"]["catalog_id"],
                action_id="workspace.adopt",
                expected_action_digest=status["next_actions"][0]["action_digest"],
                arguments=status["next_actions"][0]["arguments"],
                workspace_observation=status["workspace"],
                execute=True,
            )
            self.assertEqual(prepared["intent"], "execute")
            self.assertIn("adopt", prepared["argv"])
            self.assertIn(str(state), prepared["argv"])

    def test_public_reopen_rediscovers_a_moved_checkout_without_rebinding_it(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace, state, adopted = self._adopt(base)
            binding_id = adopted["adoption"]["binding_id"]
            session_id = adopted["session"]["session_id"]
            original_workspace_id = adopted["workspace"]["workspace_id"]
            original_workspace_root = adopted["workspace"]["root"]
            moved = base / "moved-workspace"
            workspace.rename(moved)

            missing_out, missing_err = StringIO(), StringIO()
            self.assertEqual(
                reopen_main(
                    [binding_id, "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=missing_out,
                    error=missing_err,
                ),
                2,
            )
            self.assertIn("workspace", missing_err.getvalue().casefold())

            reopened_out, reopened_err = StringIO(), StringIO()
            self.assertEqual(
                reopen_main(
                    [
                        binding_id,
                        "--workspace",
                        str(moved),
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=reopened_out,
                    error=reopened_err,
                ),
                0,
                reopened_err.getvalue(),
            )
            reopened = json.loads(reopened_out.getvalue())
            self.assertEqual(reopened["session"]["session_id"], session_id)
            self.assertEqual(
                reopened["adoption"]["adopted_workspace_id"],
                original_workspace_id,
            )
            self.assertEqual(
                reopened["adoption"]["adopted_workspace_root"],
                original_workspace_root,
            )
            self.assertEqual(reopened["workspace"]["root"], str(moved.resolve()))
            self.assertNotEqual(
                reopened["workspace"]["workspace_id"], original_workspace_id
            )
            self.assertEqual(reopened["adoption"]["freshness"], "stale")
            self.assertEqual(
                reopened["adoption"]["recovery_reasons"],
                ["WORKSPACE_LOCATION_CHANGED"],
            )
            for job in reopened["jobs"]:
                self.assertEqual(job["state"], "unavailable")
                self.assertIn("ADOPTION_BINDING_STALE", job["blockers"])
                self.assertIn("ADOPTION_RECOVERY_REQUIRED", job["blockers"])

    def test_public_session_run_uses_exact_live_console_owner_custody(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace, state, adopted = self._adopt(Path(temporary))
            session_id = adopted["session"]["session_id"]
            store = WorkSessionStore(state)
            actions = {
                row["action_id"]: row
                for row in store.status(session_id)["next_actions"]
            }
            self.assertIn("doctor.inspect", actions)
            self.assertEqual(actions["doctor.inspect"]["owner_id"], "project-intelligence")
            self.assertEqual(actions["doctor.inspect"]["availability"], "experimental")

            output, error = StringIO(), StringIO()
            code = session_main(
                [
                    "run",
                    session_id,
                    "doctor.inspect",
                    "--execute",
                    "--state-root",
                    str(state),
                    "--json",
                ],
                root=ROOT,
                output=output,
                error=error,
            )
            self.assertEqual(code, 0, error.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(result["work_session"]["lifecycle"], "complete")
            self.assertEqual(result["live_console_owner_ref"]["owner_id"], "workbench-shell")
            self.assertEqual(result["live_console_owner_ref"]["last_verified_state"], "complete")
            self.assertTrue(result["live_console_owner_ref"]["uri"].startswith("file:"))
            kinds = [
                row["kind"] for row in store.timeline(session_id)["events"]
            ]
            self.assertIn("owner-execution-bound", kinds)
            self.assertEqual(kinds[-1], "owner-result-bound")

            reopened_out, reopened_err = StringIO(), StringIO()
            self.assertEqual(
                reopen_main(
                    [
                        adopted["adoption"]["binding_id"],
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=reopened_out,
                    error=reopened_err,
                ),
                0,
                reopened_err.getvalue(),
            )
            reopened = json.loads(reopened_out.getvalue())
            self.assertEqual(reopened["session"]["session_id"], session_id)
            self.assertEqual(reopened["session"]["freshness"], "current")
            self.assertEqual(reopened["adoption"]["freshness"], "current")
            self.assertNotIn(
                "OWNER_RECORD_REVISIONS_CHANGED",
                reopened["adoption"]["stale_reasons"],
            )
            self.assertTrue(workspace.is_dir())

    def test_physical_cleanroom_job_runs_through_public_session_custody(self) -> None:
        self._physical_cleanroom_tools()
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary) / "state"
            adopted_out, adopted_err = StringIO(), StringIO()
            self.assertEqual(
                adopt_main(
                    [str(CLEANROOM_FIXTURE), "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=adopted_out,
                    error=adopted_err,
                ),
                0,
                adopted_err.getvalue(),
            )
            adopted = json.loads(adopted_out.getvalue())
            session_id = adopted["session"]["session_id"]
            action = next(
                row
                for row in WorkSessionStore(state).status(session_id)["next_actions"]
                if row["action_id"] == "cleanroom.fixture-build"
            )
            self.assertEqual(action["availability"], "experimental")
            run_out, run_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "run",
                        session_id,
                        "cleanroom.fixture-build",
                        "--execute",
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=run_out,
                    error=run_err,
                ),
                0,
                run_err.getvalue(),
            )
            result = json.loads(run_out.getvalue())
            self.assertEqual(result["work_session"]["lifecycle"], "complete")
            self.assertEqual(
                result["live_console_owner_ref"]["last_verified_state"],
                "complete",
            )
            self.assertFalse((CLEANROOM_FIXTURE / ".gradle").exists())

    def test_physical_frontend_kill_reopens_exact_cleanroom_custody(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX cross-process custody probe")
        self._physical_cleanroom_tools()
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            state = Path(temporary) / "state"
            adopted_out, adopted_err = StringIO(), StringIO()
            self.assertEqual(
                adopt_main(
                    [str(CLEANROOM_FIXTURE), "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=adopted_out,
                    error=adopted_err,
                ),
                0,
                adopted_err.getvalue(),
            )
            session_id = json.loads(adopted_out.getvalue())["session"]["session_id"]
            environment = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            frontend = subprocess.Popen(
                [
                    sys.executable,
                    str(ROUTER),
                    "session",
                    "run",
                    session_id,
                    "cleanroom.fixture-build",
                    "--execute",
                    "--state-root",
                    str(state),
                    "--json",
                ],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid: int | None = None
            process_group: int | None = None
            live_session_id: str | None = None
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    for manifest in list_sessions(state):
                        try:
                            candidate_pid = manifest["command"]["pid"]
                            candidate_group = manifest["command"]["process_group_id"]
                            candidate_session_id = manifest["session_id"]
                        except KeyError:
                            continue
                        if not isinstance(candidate_pid, int):
                            continue
                        try:
                            command_line = Path(
                                f"/proc/{candidate_pid}/cmdline"
                            ).read_bytes()
                        except OSError:
                            continue
                        if b"process_gate.py" in command_line:
                            continue
                        child_pid = candidate_pid
                        process_group = candidate_group
                        live_session_id = candidate_session_id
                        break
                    if child_pid is not None:
                        break
                    if frontend.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(child_pid, "physical owner child never crossed the gate")
                self.assertIsNone(frontend.poll(), "frontend exited before the kill probe")
                os.kill(frontend.pid, signal.SIGKILL)
                frontend.communicate(timeout=10)
                self.assertEqual(frontend.returncode, -signal.SIGKILL)
                assert live_session_id is not None
                owner = live_console_owner_reference(state, live_session_id)
                self.assertEqual(owner["last_verified_state"], "running")

                preview_out, preview_err = StringIO(), StringIO()
                self.assertEqual(
                    session_main(
                        [
                            "recover",
                            session_id,
                            "--state-root",
                            str(state),
                            "--json",
                        ],
                        root=ROOT,
                        output=preview_out,
                        error=preview_err,
                    ),
                    0,
                    preview_err.getvalue(),
                )
                preview = json.loads(preview_out.getvalue())
                self.assertTrue(preview["required"])
                self.assertEqual(preview["owner_resolution"], "verified")
                self.assertEqual(
                    preview["owner_record_refs"][0]["last_verified_state"],
                    "running",
                )
                self.assertEqual(
                    preview["safe_actions"][0]["action_id"],
                    "workbench.session.recover",
                )

                resume_out, resume_err = StringIO(), StringIO()
                self.assertEqual(
                    session_main(
                        [
                            "resume",
                            session_id,
                            "--frontend",
                            "vscode",
                            "--state-root",
                            str(state),
                            "--json",
                        ],
                        root=ROOT,
                        output=resume_out,
                        error=resume_err,
                    ),
                    0,
                    resume_err.getvalue(),
                )
                assert process_group is not None
                os.killpg(process_group, signal.SIGTERM)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    os.killpg(process_group, signal.SIGKILL)

                apply_out, apply_err = StringIO(), StringIO()
                self.assertEqual(
                    session_main(
                        [
                            "recover",
                            session_id,
                            "--apply",
                            "--frontend",
                            "intellij-community",
                            "--state-root",
                            str(state),
                            "--json",
                        ],
                        root=ROOT,
                        output=apply_out,
                        error=apply_err,
                    ),
                    0,
                    apply_err.getvalue(),
                )
                recovered = json.loads(apply_out.getvalue())
                self.assertEqual(recovered["lifecycle"], "recoverable")
                timeline = WorkSessionStore(state).timeline(session_id)["events"]
                self.assertIn("vscode", [row["frontend"]["kind"] for row in timeline])
                self.assertEqual(
                    timeline[-1]["frontend"]["kind"], "intellij-community"
                )
                self.assertFalse((CLEANROOM_FIXTURE / ".gradle").exists())
            finally:
                if frontend.poll() is None:
                    frontend.kill()
                try:
                    frontend.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    frontend.kill()
                    frontend.communicate(timeout=10)
                if process_group is not None:
                    try:
                        os.killpg(process_group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_frontend_adapters_share_one_session_port_and_exact_journal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            _, state, adopted = self._adopt(Path(temporary))
            session_id = adopted["session"]["session_id"]
            for action, frontend in (
                ("resume", "vscode"),
                ("close", "intellij-community"),
            ):
                output, error = StringIO(), StringIO()
                code = session_main(
                    [
                        action,
                        session_id,
                        "--frontend",
                        frontend,
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                )
                self.assertEqual(code, 0, error.getvalue())
            timeline = WorkSessionStore(state).timeline(session_id)["events"]
            self.assertEqual(
                [row["frontend"]["kind"] for row in timeline[-2:]],
                ["vscode", "intellij-community"],
            )
            self.assertEqual(
                [row["kind"] for row in timeline[-2:]],
                ["frontend-reopened", "session-closed"],
            )

    def test_artifact_route_reads_historical_owner_range_through_core_custody(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            state = base / "state"
            catalog = build_catalog(ROOT)
            store = WorkSessionStore(state)
            created = store.create(
                task={"task_id": "task:artifact", "owner_id": "workbench-shell"},
                workspace={
                    "identity_id": "workspace:artifact",
                    "canonical_root": str(workspace),
                    "source_revision": "source:artifact",
                    "dirty_fingerprint": None,
                },
                identities={
                    "core_id": "core:test",
                    "catalog_id": catalog.catalog_digest,
                    "host_adapter_id": "host:test",
                    "platform_profile_id": None,
                    "pack_profile_id": None,
                },
                frontend={
                    "frontend_id": "frontend:test",
                    "kind": "test",
                    "version": "test",
                },
            )
            session_id = created["session_id"]
            status = store.status(session_id)
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
                expected_sequence=status["latest_sequence"],
                frontend={
                    "frontend_id": "frontend:test",
                    "kind": "test",
                    "version": "test",
                },
                kind="actions-ranked",
                next_actions=[action],
                catalog=catalog,
            )
            prepared = store.prepare_catalog_action(
                session_id,
                expected_sequence=ranked["summary"]["latest_sequence"],
                catalog=catalog,
                expected_catalog_digest=status["identities"]["catalog_id"],
                action_id=action["action_id"],
                expected_action_digest=action["action_digest"],
                arguments=action["arguments"],
                workspace_observation=status["workspace"],
                execute=True,
            )
            retained = RetainedSession(
                root=state,
                command_id=action["action_id"],
                argv=redact_argv(prepared["argv"], command.fields),
                cwd=workspace,
                intent=prepared["intent"],
                label="artifact navigation probe",
            )
            historical = live_console_owner_reference(state, retained.session_id)
            store.bind_owner_execution(
                prepared,
                catalog=catalog,
                frontend={
                    "frontend_id": "frontend:test",
                    "kind": "test",
                    "version": "test",
                },
                owner_record_refs=[historical],
                owner_execution_verifier=lambda row: live_console_execution_reference(
                    state, row
                ),
            )
            process, release = start_bound_fixture_process(retained, state)
            locator = retained.write_raw("stdout", b"owner bytes\n")
            retained.record_event(
                {
                    "format_version": "workbench-live-console-event-v1",
                    "event_id": "event:1",
                    "sequence": 1,
                    "ingested_at": "2026-08-21T00:00:00Z",
                    "monotonic_ns": 1,
                    "source_timestamp": None,
                    "source": action["action_id"],
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
                    "message": "owner bytes",
                    "parse_provenance": "raw",
                    "classification_basis": [],
                    "cluster_key": "sha256:" + "1" * 64,
                    "signal": False,
                    "outcome_failure": False,
                    "source_locators": [],
                    "limitations": [],
                }
            )
            control_locator = retained.write_raw(
                "stderr", b"\x1b]52;c;WkZJPT0=\x07\n"
            )
            retained.record_event(
                {
                    "format_version": "workbench-live-console-event-v1",
                    "event_id": "event:2",
                    "sequence": 2,
                    "ingested_at": "2026-08-21T00:00:01Z",
                    "monotonic_ns": 2,
                    "source_timestamp": None,
                    "source": action["action_id"],
                    "stream": "stderr",
                    "raw_locator": {
                        "artifact": control_locator.path,
                        "byte_start": control_locator.byte_start,
                        "byte_end": control_locator.byte_end,
                        "line": 1,
                        "chunk": 1,
                        "boundary": "lf",
                    },
                    "kind": "text",
                    "severity": "warning",
                    "subsystem": "generic",
                    "logger": None,
                    "thread": None,
                    "message": "terminal control bytes",
                    "parse_provenance": "raw",
                    "classification_basis": [],
                    "cluster_key": "sha256:" + "2" * 64,
                    "signal": False,
                    "outcome_failure": False,
                    "source_locators": [],
                    "limitations": [],
                }
            )
            release.write_text("go\n", encoding="utf-8")
            process.wait(timeout=10)
            retained.finish(
                state="complete",
                process_exit_code=process.returncode,
                effective_exit_code=0,
                outcome="complete",
            )

            listed_out, listed_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "artifact",
                        session_id,
                        historical["record_id"],
                        historical["digest"],
                        "--frontend",
                        "vscode",
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=listed_out,
                    error=listed_err,
                ),
                0,
                listed_err.getvalue(),
            )
            listed = json.loads(listed_out.getvalue())
            self.assertEqual(
                listed["format_version"], "workbench-owner-artifact-events-v1"
            )
            self.assertEqual(listed["session_id"], session_id)
            self.assertEqual(listed["events"][0]["event_id"], "event:1")
            self.assertNotEqual(
                listed["current_owner_digest"], historical["digest"]
            )

            range_out, range_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "artifact",
                        session_id,
                        historical["record_id"],
                        historical["digest"],
                        "event:1",
                        "--frontend",
                        "intellij-community",
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=range_out,
                    error=range_err,
                ),
                0,
                range_err.getvalue(),
            )
            selected = json.loads(range_out.getvalue())
            self.assertEqual(selected["content_base64"], "b3duZXIgYnl0ZXMK")
            self.assertEqual(selected["utf8"], "owner bytes\n")

            plain_out, plain_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "artifact",
                        session_id,
                        historical["record_id"],
                        historical["digest"],
                        "event:2",
                        "--state-root",
                        str(state),
                    ],
                    root=ROOT,
                    output=plain_out,
                    error=plain_err,
                ),
                0,
                plain_err.getvalue(),
            )
            self.assertNotIn("\x1b", plain_out.getvalue())
            self.assertNotIn("\x07", plain_out.getvalue())
            self.assertIn("\\u001b", plain_out.getvalue())
            self.assertIn("\\u0007", plain_out.getvalue())

            rejected_out, rejected_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "artifact",
                        session_id,
                        historical["record_id"],
                        "sha256:" + "f" * 64,
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=rejected_out,
                    error=rejected_err,
                ),
                2,
            )
            self.assertIn("not retained exactly", rejected_err.getvalue())

    def test_owner_bind_cas_failure_never_calls_the_executor(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            _, state, adopted = self._adopt(Path(temporary))
            session_id = adopted["session"]["session_id"]
            output, error = StringIO(), StringIO()
            conflict = WorkSessionConflictError(
                "work-session.stale-sequence",
                "forced prelaunch sequence loss",
                retryable=True,
            )
            with patch.object(
                WorkSessionStore,
                "bind_owner_execution",
                side_effect=conflict,
            ), patch(
                "workbench_shell.product_spine_cli.supervise_process"
            ) as execute:
                code = session_main(
                    [
                        "run",
                        session_id,
                        "doctor.inspect",
                        "--execute",
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                )
            self.assertEqual(code, 2)
            execute.assert_not_called()
            self.assertIn("forced prelaunch sequence loss", error.getvalue())

    def test_recovery_verifies_mutable_owner_revision_then_reconciles_terminal(self) -> None:
        if os.name != "posix":
            self.skipTest("live process identity probe")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace, state, adopted = self._adopt(Path(temporary))
            session_id = adopted["session"]["session_id"]
            store = WorkSessionStore(state)
            status = store.status(session_id)
            action = next(
                row for row in status["next_actions"]
                if row["action_id"] == "doctor.inspect"
            )
            catalog = build_catalog(ROOT)
            prepared = store.prepare_catalog_action(
                session_id,
                expected_sequence=status["latest_sequence"],
                catalog=catalog,
                expected_catalog_digest=status["identities"]["catalog_id"],
                action_id=action["action_id"],
                expected_action_digest=action["action_digest"],
                arguments=action["arguments"],
                workspace_observation=status["workspace"],
                execute=True,
            )
            command = catalog.command(action["action_id"])
            retained = RetainedSession(
                root=state,
                command_id=action["action_id"],
                argv=redact_argv(prepared["argv"], command.fields),
                cwd=workspace,
                intent=prepared["intent"],
                label="recovery transition probe",
            )
            allocated = live_console_owner_reference(state, retained.session_id)
            store.bind_owner_execution(
                prepared,
                catalog=catalog,
                frontend={"frontend_id": "frontend:test", "kind": "test", "version": "test"},
                owner_record_refs=[allocated],
                owner_execution_verifier=lambda row: live_console_execution_reference(
                    state, row
                ),
            )
            process, release = start_bound_fixture_process(retained, state)

            preview_out, preview_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    ["recover", session_id, "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=preview_out,
                    error=preview_err,
                ),
                0,
                preview_err.getvalue(),
            )
            preview = json.loads(preview_out.getvalue())
            self.assertEqual(preview["owner_resolution"], "verified")
            self.assertEqual(preview["owner_record_refs"][0]["last_verified_state"], "running")
            self.assertEqual(preview["safe_actions"][0]["action_id"], "workbench.session.recover")

            apply_out, apply_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "recover",
                        session_id,
                        "--apply",
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=apply_out,
                    error=apply_err,
                ),
                0,
                apply_err.getvalue(),
            )
            self.assertEqual(json.loads(apply_out.getvalue())["lifecycle"], "recoverable")

            release.write_text("go\n", encoding="utf-8")
            process.wait(timeout=10)
            retained.finish(
                state="complete",
                process_exit_code=process.returncode,
                effective_exit_code=0,
                outcome="complete",
            )
            terminal_out, terminal_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "recover",
                        session_id,
                        "--apply",
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=terminal_out,
                    error=terminal_err,
                ),
                0,
                terminal_err.getvalue(),
            )
            terminal = json.loads(terminal_out.getvalue())
            self.assertEqual(terminal["lifecycle"], "complete")
            self.assertEqual(terminal["result_refs"][0]["owner_record_ref"]["record_id"], retained.session_id)

    def test_moved_workspace_preserves_historical_live_owner_recovery(self) -> None:
        if os.name != "posix":
            self.skipTest("live process identity probe")
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace, state, adopted = self._adopt(base)
            session_id = adopted["session"]["session_id"]
            store = WorkSessionStore(state)
            status = store.status(session_id)
            action = next(
                row
                for row in status["next_actions"]
                if row["action_id"] == "doctor.inspect"
            )
            catalog = build_catalog(ROOT)
            prepared = store.prepare_catalog_action(
                session_id,
                expected_sequence=status["latest_sequence"],
                catalog=catalog,
                expected_catalog_digest=status["identities"]["catalog_id"],
                action_id=action["action_id"],
                expected_action_digest=action["action_digest"],
                arguments=action["arguments"],
                workspace_observation=status["workspace"],
                execute=True,
            )
            command = catalog.command(action["action_id"])
            retained = RetainedSession(
                root=state,
                command_id=action["action_id"],
                argv=redact_argv(prepared["argv"], command.fields),
                cwd=workspace,
                intent=prepared["intent"],
                label="moved workspace custody probe",
            )
            allocated = live_console_owner_reference(state, retained.session_id)
            store.bind_owner_execution(
                prepared,
                catalog=catalog,
                frontend={"frontend_id": "frontend:test", "kind": "test", "version": "test"},
                owner_record_refs=[allocated],
                owner_execution_verifier=lambda row: live_console_execution_reference(
                    state, row
                ),
            )
            process, _release = start_bound_fixture_process(retained, state)
            moved = base / "moved-workspace"
            workspace.rename(moved)

            resume_out, resume_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    [
                        "resume",
                        session_id,
                        "--workspace",
                        str(moved),
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    root=ROOT,
                    output=resume_out,
                    error=resume_err,
                ),
                0,
                resume_err.getvalue(),
            )
            preview_out, preview_err = StringIO(), StringIO()
            self.assertEqual(
                session_main(
                    ["recover", session_id, "--state-root", str(state), "--json"],
                    root=ROOT,
                    output=preview_out,
                    error=preview_err,
                ),
                0,
                preview_err.getvalue(),
            )
            preview = json.loads(preview_out.getvalue())
            self.assertEqual(preview["owner_resolution"], "verified")
            self.assertEqual(
                preview["owner_record_refs"][0]["last_verified_state"], "running"
            )
            self.assertEqual(
                store.status(session_id)["workspace"]["canonical_root"],
                str(moved.resolve()),
            )
            process.terminate()
            process.wait(timeout=10)
            retained.finish(
                state="cancelled",
                process_exit_code=process.returncode,
                effective_exit_code=130,
                outcome="cancelled",
                cancellation="interrupt-requested",
            )


if __name__ == "__main__":
    unittest.main()
