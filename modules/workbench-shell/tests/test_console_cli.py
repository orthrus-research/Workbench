from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_shell.console_cli import build_parser, run
from workbench_shell.catalog import build_catalog
from workbench_shell.console_cli import TerminalApplication
from workbench_core.sessions import RetainedSession


ROOT = Path(__file__).resolve().parents[3]


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class CliTests(unittest.TestCase):
    def test_parser_constructs_all_noninteractive_surfaces(self) -> None:
        parser = build_parser()
        for argv in (
            ["catalog"],
            ["run", "doctor.inspect", "--print-command"],
            ["watch", "--", "echo", "hello"],
            ["ingest", "a.log"],
            ["sessions"],
            ["replay"],
        ):
            with self.subTest(argv=argv):
                self.assertIsNotNone(parser.parse_args(argv).action)

    def test_catalog_json_covers_every_implemented_module_and_planned_horizon(self) -> None:
        output = io.StringIO()
        code = run(["catalog", "--json"], root=ROOT, output=output, error=io.StringIO())
        self.assertEqual(code, 0)
        value = json.loads(output.getvalue())
        suites = {row["suite_id"]: row for row in value["suites"]}
        for suite in ("shell", "atlas", "blueprints", "manuals", "mixin", "world-studio"):
            self.assertGreater(suites[suite]["command_count"], 0)
        self.assertEqual(suites["sentinel"]["availability"], "experimental")
        self.assertEqual(suites["relay"]["availability"], "experimental")
        self.assertEqual(suites["sentinel"]["command_count"], 1)
        self.assertEqual(suites["relay"]["command_count"], 1)
        self.assertGreaterEqual(len(value["commands"]), 90)
        self.assertEqual(value["format_version"], "workbench-live-console-command-catalog-v2")
        self.assertRegex(value["catalog_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_catalog_exposes_explicit_existing_pack_qualification(self) -> None:
        catalog = build_catalog(ROOT)
        command = catalog.command("project.qualify")
        workspace = str(ROOT)
        argv, intent = command.build_argv(
            {
                "workspace": workspace,
                "profile": "supersymmetry",
                "plan": True,
                "json": True,
            },
            root=ROOT,
            execute=False,
        )
        self.assertEqual(intent, "inert")
        self.assertEqual(
            argv[2:],
            [
                "project",
                "qualify",
                workspace,
                "--profile",
                "supersymmetry",
                "--plan",
                "--json",
            ],
        )
        self.assertEqual(command.risk, "writes-output")
        self.assertIn("never adds or changes", " ".join(command.limitations))

    def test_bound_review_fails_closed_on_catalog_action_or_argv_drift(self) -> None:
        catalog = build_catalog(ROOT)
        command = catalog.command("doctor.inspect")
        catalog_digest = catalog.catalog_digest
        action_digest = command.action_digest(root=ROOT)
        review_output = io.StringIO()
        review_args = [
            "run",
            command.command_id,
            "--set",
            "workspace=.",
            "--expect-catalog-digest",
            catalog_digest,
            "--expect-action-digest",
            action_digest,
            "--review-json",
        ]
        self.assertEqual(
            run(review_args, root=ROOT, output=review_output, error=io.StringIO()),
            0,
        )
        review = json.loads(review_output.getvalue())
        self.assertEqual(
            review["format_version"],
            "workbench-live-console-command-review-v2",
        )
        self.assertEqual(review["catalog_digest"], catalog_digest)
        self.assertEqual(review["action_digest"], action_digest)
        self.assertEqual(review["risk"], command.risk)
        self.assertEqual(review["preview"], command.preview)
        self.assertIn("doctor", review["preview_command"])
        self.assertIn("doctor", review["execute_command"])

        bound = [
            "run",
            command.command_id,
            "--set",
            "workspace=.",
            "--expect-catalog-digest",
            catalog_digest,
            "--expect-action-digest",
            action_digest,
            "--expect-review-digest",
            review["review_digest"],
            "--print-command",
        ]
        self.assertEqual(
            run(bound, root=ROOT, output=io.StringIO(), error=io.StringIO()),
            0,
        )

        drift_error = io.StringIO()
        drifted = list(bound)
        drifted[drifted.index("workspace=.")] = "workspace=modules"
        self.assertEqual(
            run(drifted, root=ROOT, output=io.StringIO(), error=drift_error),
            2,
        )
        self.assertIn("review changed after review", drift_error.getvalue())

        action_error = io.StringIO()
        stale_action = list(bound)
        stale_action[stale_action.index(action_digest)] = "sha256:" + "0" * 64
        self.assertEqual(
            run(stale_action, root=ROOT, output=io.StringIO(), error=action_error),
            2,
        )
        self.assertIn("action changed after review", action_error.getvalue())

    def test_partial_bound_run_is_rejected_but_direct_cli_remains_available(self) -> None:
        catalog = build_catalog(ROOT)
        command = catalog.command("doctor.inspect")
        error = io.StringIO()
        self.assertEqual(
            run(
                [
                    "run",
                    command.command_id,
                    "--expect-catalog-digest",
                    catalog.catalog_digest,
                    "--print-command",
                ],
                root=ROOT,
                output=io.StringIO(),
                error=error,
            ),
            2,
        )
        self.assertIn("requires catalog, action, and review", error.getvalue())
        self.assertEqual(
            run(
                ["run", command.command_id, "--print-command"],
                root=ROOT,
                output=io.StringIO(),
                error=io.StringIO(),
            ),
            0,
        )

    def test_inert_command_review_never_launches_mutator(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        code = run(
            [
                "run",
                "world-studio.iterate",
                "--set",
                "label=must-not-run",
                "--set",
                "profile=supersymmetry",
            ],
            root=ROOT,
            output=output,
            error=error,
        )
        self.assertEqual(code, 0)
        self.assertIn("Inert command review only", output.getvalue())
        self.assertFalse((ROOT / ".workbench/iterations/worldgen/must-not-run").exists())

    def test_missing_required_wizard_input_fails_before_launch(self) -> None:
        error = io.StringIO()
        code = run(
            ["run", "runs.managed", "--set", "recipe=fast", "--no-retain"],
            root=ROOT,
            output=io.StringIO(),
            error=error,
        )
        self.assertEqual(code, 2)
        self.assertIn("requires --profile", error.getvalue())

    def test_watch_uses_exact_argv_without_shell_interpretation(self) -> None:
        output = io.StringIO()
        code = run(
            [
                "watch",
                "--no-retain",
                "--console",
                "plain",
                "--view",
                "all",
                "--",
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1])",
                "a; echo not-executed",
            ],
            root=ROOT,
            output=output,
            error=io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertIn("a; echo not-executed", output.getvalue())

    def test_ingest_and_literal_search_work_without_tty(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            path = Path(temporary) / "latest.log"
            path.write_text("boring\nBUILD FAILED in 2s\n", encoding="utf-8")
            output = io.StringIO()
            code = run(
                [
                    "ingest",
                    str(path),
                    "--no-retain",
                    "--console",
                    "plain",
                    "--view",
                    "all",
                    "--search",
                    "BUILD FAILED",
                ],
                root=ROOT,
                output=output,
                error=io.StringIO(),
            )
            self.assertEqual(code, 1)
            self.assertIn("BUILD FAILED", output.getvalue())
            self.assertNotIn("boring", output.getvalue())

    def test_message_only_mode_is_complete_by_default(self) -> None:
        output = io.StringIO()
        code = run(
            [
                "watch",
                "--no-retain",
                "--console",
                "messages",
                "--",
                sys.executable,
                "-c",
                "print('ordinary line')",
            ],
            root=ROOT,
            output=output,
            error=io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertIn("ordinary line", output.getvalue())
        self.assertIn("stage child-process started", output.getvalue())

    def test_no_action_on_redirected_input_is_noninteractive_and_actionable(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        code = run([], root=ROOT, input_stream=io.StringIO(), output=output, error=error)
        self.assertEqual(code, 2)
        self.assertIn("Workbench console tool suites", output.getvalue())
        self.assertIn("interactive menus require a TTY", error.getvalue())

    def test_wizard_exposes_owner_json_option(self) -> None:
        output = TtyStringIO()
        code = run(
            ["wizard", "storage.list"],
            root=ROOT,
            # Keep the default storage scope and root, select JSON, then decline execution.
            input_stream=TtyStringIO("\n\ny\nn\n"),
            output=output,
            error=io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertIn("storage list --json", output.getvalue())

    def test_wizard_eof_cancels_without_looping_or_running(self) -> None:
        output = TtyStringIO()
        error = io.StringIO()
        code = run(
            ["wizard", "storage.list"],
            root=ROOT,
            input_stream=TtyStringIO(),
            output=output,
            error=error,
        )
        self.assertEqual(code, 0)
        self.assertIn("input closed", error.getvalue())

    def test_command_palette_rejects_zero_and_negative_indexes(self) -> None:
        catalog = build_catalog(ROOT)
        for selected in ("0", "-1"):
            with self.subTest(selected=selected):
                app = TerminalApplication(
                    root=ROOT,
                    catalog=catalog,
                    input_stream=TtyStringIO(selected + "\n\n"),
                    output=TtyStringIO(),
                    error=io.StringIO(),
                )
                self.assertIsNone(app._select_command(catalog.for_suite("storage")))

    def test_interactive_settings_cover_renderer_retention_and_filters(self) -> None:
        app = TerminalApplication(
            root=ROOT,
            catalog=build_catalog(ROOT),
            input_stream=TtyStringIO(
                "messages\nn\nall\nnever\n-\nwarning\nworldgen,mixin\n\n"
            ),
            output=TtyStringIO(),
            error=io.StringIO(),
        )
        app._settings()
        namespace = app._console_namespace()
        self.assertEqual(namespace.renderer_mode, "messages")
        self.assertTrue(namespace.no_retain)
        self.assertEqual(namespace.view, "all")
        self.assertEqual(namespace.color, "never")
        self.assertEqual(namespace.minimum_severity, "warning")
        self.assertEqual(namespace.subsystem, ["worldgen", "mixin"])

    def test_sessions_and_replay_use_same_retained_events(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="fixture",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="session-cli-001",
            )
            session.write_raw("stdout", b"needle")
            session.record_event(
                {
                    "format_version": "workbench-live-console-event-v1",
                    "event_id": "event-000000000001",
                    "sequence": 1,
                    "ingested_at": "2026-08-04T00:00:00Z",
                    "monotonic_ns": 1,
                    "source_timestamp": None,
                    "source": "fixture",
                    "stream": "stdout",
                    "raw_locator": {"artifact": "stdout.raw", "byte_start": 0, "byte_end": 6, "line": 1, "chunk": 1, "boundary": "lf"},
                    "kind": "text",
                    "severity": "unknown",
                    "subsystem": "generic",
                    "logger": None,
                    "thread": None,
                    "message": "needle",
                    "parse_provenance": "raw",
                    "classification_basis": [],
                    "cluster_key": "sha256:" + "0" * 64,
                    "signal": True,
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
            listing = io.StringIO()
            self.assertEqual(run(["sessions"], root=root, output=listing, error=io.StringIO()), 0)
            self.assertIn("session-cli-001", listing.getvalue())
            replay = io.StringIO()
            self.assertEqual(
                run(
                    ["replay", "session-cli", "--console", "plain", "--view", "all", "--search", "needle"],
                    root=root,
                    output=replay,
                    error=io.StringIO(),
                ),
                0,
            )
            self.assertIn("needle", replay.getvalue())

    def test_catalog_sensitive_value_is_redacted_in_real_session_manifest(self) -> None:
        private_profile = "PrivateProfile"
        observed_child_argv: list[str] = []

        def complete_without_launch(argv, **kwargs):
            observed_child_argv.extend(argv)
            session = kwargs["session"]
            release = root / "release-mocked-child"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib,time\n"
                        "p=pathlib.Path(" + repr(str(release)) + ")\n"
                        "while not p.exists(): time.sleep(0.01)\n"
                    ),
                ],
                start_new_session=os.name == "posix",
            )
            session.bind_process(
                process.pid,
                process.pid if os.name == "posix" else None,
            )
            release.write_text("go\n", encoding="utf-8")
            process.wait(timeout=10)
            value = session.finish(
                state="complete",
                process_exit_code=process.returncode,
                effective_exit_code=0,
                outcome="complete",
            )
            return type("Result", (), {"effective_exit_code": 0, "session": value})()

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            with patch(
                "workbench_shell.console_cli.supervise_process",
                side_effect=complete_without_launch,
            ):
                code = run(
                    [
                        "run",
                        "shell.material-fluid-run",
                        "--execute",
                        "--set",
                        f"workspace={root / 'workspace'}",
                        "--set",
                        "name=Pilot Coolant",
                        "--set",
                        "color=0x425d73",
                        "--set",
                        "plan_id=sha256:" + "4" * 64,
                        "--set",
                        f"launcher_executable={root / 'launcher'}",
                        "--set",
                        f"launcher_root={root / 'launcher-root'}",
                        "--set",
                        f"launcher_profile={private_profile}",
                        "--console",
                        "plain",
                    ],
                    root=root,
                    output=io.StringIO(),
                    error=io.StringIO(),
                )
            self.assertEqual(0, code)
            self.assertIn(private_profile, observed_child_argv)
            manifests = list(
                (root / ".workbench/sessions/live-console").glob(
                    "*/session-v1.json"
                )
            )
            self.assertEqual(1, len(manifests))
            manifest_text = manifests[0].read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            retained_argv = manifest["command"]["argv"]
            profile_index = retained_argv.index("--launcher-profile")
            self.assertEqual("<redacted>", retained_argv[profile_index + 1])
            self.assertNotIn(private_profile, manifest_text)


if __name__ == "__main__":
    unittest.main()
