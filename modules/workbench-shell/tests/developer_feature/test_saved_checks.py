"""Saved-candidate execution with a real supervised fixture, never a game claim."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from workbench_core import check_storage
from workbench_profile_supersymmetry import developer_checks as policy
from workbench_project_intelligence.working_tree import capture_source_inputs
from workbench_shell.developer_checks import run_checks
from workbench_shell.developer_context import (
    DeveloperSelection,
    verify_developer_owner_reference,
)
from test_developer_feature import ROOT, _checkout

VALIDATE_IMAGE = policy.validate_image

FIXTURE = """import json, pathlib, re, time
root = pathlib.Path('.')
nonce = re.search(r'nonce: "([0-9a-f]+)"', (root/'groovy/workbenchChecks/Observe.groovy').read_text()).group(1)
(root/'logs').mkdir()
message = '[WORKBENCH-SAVED-CHECK]' + json.dumps(dict(nonce=nonce, side='CLIENT', items=7, fluids=8))
error = '[CLIENT/ERROR] groovy/postInit/chemistry/Probe.groovy: 1: compilation failed' if 'BROKEN' in (root/'groovy/postInit/chemistry/Probe.groovy').read_text() else ''
(root/'logs/latest.log').write_text(('Forge Mod Loader has successfully loaded 2 mods\\n' + message + '\\n') if not error else 'waiting before postInit\\n')
(root/'logs/groovy.log').write_text(error + '\\n')
print(error, flush=True)
time.sleep(30)
"""


class SavedCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.pack = _checkout(self.base)
        (self.pack / "groovy/runConfig.json").write_text(
            json.dumps({"loaders": {"postInit": ["postInit/"]}})
        )
        self.selection = DeveloperSelection(
            self.pack.as_uri(), "supersymmetry", "cleanroom", "cleanroom-provisional"
        )
        self.state = self.base / "private"
        self.root = self.state / "developer-checks"
        self.template = self.base / "image"
        self.template.mkdir()
        (self.template / "fixture.py").write_text(FIXTURE)
        self.image = check_storage.import_image(
            self.root,
            self.template,
            Path(sys.executable).resolve(),
            ["fixture.py"],
            policy.binding(capture_source_inputs(self.pack)),
            excluded_roots=policy.descriptor()["excluded_roots"],
        )
        # Only the installed game prerequisites are replaced by a fixture. The
        # actual profile probe/decoder, source capture, Core process/storage and
        # retained result paths are exercised end to end.
        patcher = patch.object(
            policy, "validate_image", return_value=self.image["binding"]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        provenance_patcher = patch.object(policy, "provenance", return_value={"platform": {"id": "fixture", "version": "1"}, "pack": {"name": "fixture", "version": "test"}, "dependencies": []})
        provenance_patcher.start()
        self.addCleanup(provenance_patcher.stop)

    def command(self, *args):
        return run_checks(self.selection, list(args), state_root=self.state)

    def prepare(self):
        return self.command("prepare", "--image", self.image["id"], "--timeout", "3")[
            "result"
        ]

    def execute(self, request):
        return self.command(
            "execute", request["attempt_id"], "--confirm", request["id"]
        )

    def test_saved_add_delete_modes_execute_and_reopen_without_source_mutation(self):
        path = self.pack / "groovy/new.groovy"
        path.write_text("// developer addition\n")
        path.chmod(0o755)
        deleted = self.pack / "groovy/material/PetrochemistryMaterials.groovy"
        deleted.unlink()
        before = capture_source_inputs(self.pack)
        request = self.prepare()
        self.assertEqual(request["state"], "prepared-not-run")
        self.assertFalse(request["authority"]["runtime_launched"])
        response = self.execute(request)
        result = response["result"]
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(result["cleanup"]["state"], "trashed", result)
        self.assertEqual(before, capture_source_inputs(self.pack))
        self.assertEqual(self.command("show", request["attempt_id"])["result"], result)
        verified = verify_developer_owner_reference(
            response["owner_record_ref"], self.selection, suite_root=ROOT
        )
        self.assertEqual(verified["last_verified_state"], "completed")
        self.assertFalse((self.pack / ".workbench").exists())
        self.assertNotIn(
            deleted.relative_to(self.pack).as_posix(),
            {row["path"] for row in request["candidate"]["files"]},
        )
        self.assertEqual(
            next(
                row["mode"]
                for row in request["candidate"]["files"]
                if row["path"] == "groovy/new.groovy"
            )
            & 0o777,
            0o755,
        )

    def test_error_then_manual_fix_returns_exact_diagnostic(self):
        script = self.pack / "groovy/postInit/chemistry/Probe.groovy"
        script.write_text("BROKEN\n")
        request = self.prepare()
        result = self.execute(request)["result"]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["execution"]["stop_reason"], "failure-observed")
        self.assertEqual(result["execution"]["state"], "closed")
        finding = result["interpretation"]["findings"][0]
        self.assertEqual(result["interpretation"]["findings_count"], 1)
        self.assertEqual(len(finding["evidence"]), 2)
        self.assertEqual(
            finding["location"]["path"], script.relative_to(self.pack).as_posix()
        )
        script.write_text("// fixed manually\n")
        self.assertEqual(self.execute(self.prepare())["result"]["state"], "completed")

    def test_explicit_comparison_reopens_without_profile_and_verifies_owner_link(self):
        before = self.execute(self.prepare())["result"]
        after = self.execute(self.prepare())["result"]
        with patch("workbench_shell.developer_checks.provider", side_effect=AssertionError("no profile needed")):
            response = self.command("compare", after["attempt_id"], "--reference", before["attempt_id"])
            self.assertEqual(response["result"]["state"], "compared")
            self.assertEqual(self.command("compare", after["attempt_id"], "--reference", before["attempt_id"])["result"], response["result"])
            self.assertEqual(len(self.command("history")["result"]["runs"]), 2)
            verified = verify_developer_owner_reference(response["owner_record_ref"], self.selection, suite_root=ROOT)
            self.assertEqual(verified["last_verified_state"], "compared")
        (self.root/".workbench/check-attempts"/before["attempt_id"]/"logs/latest.log").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.command("compare", after["attempt_id"], "--reference", before["attempt_id"])
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            verify_developer_owner_reference(response["owner_record_ref"], self.selection, suite_root=ROOT)

    def test_unchanged_broken_fixed_sequence_is_directional_and_source_bound(self):
        baseline = self.execute(self.prepare())["result"]
        script = self.pack/"groovy/postInit/chemistry/Probe.groovy"
        script.write_text("BROKEN\n")
        failed = self.execute(self.prepare())["result"]
        comparison = self.command("compare", failed["attempt_id"], "--reference", baseline["attempt_id"])["result"]
        self.assertEqual(comparison["counts"]["newly-observed"], 1)
        self.assertEqual(comparison["candidate"]["state"], "failed")
        self.assertEqual(failed["diagnostics"][0]["origin"]["kind"], "pack-source")
        script.write_text("// corrected\n")
        corrected = self.execute(self.prepare())["result"]
        comparison = self.command("compare", corrected["attempt_id"], "--reference", failed["attempt_id"])["result"]
        self.assertEqual(comparison["counts"]["no-longer-observed"], 1)
        self.assertEqual(self.command("show", failed["attempt_id"])["presentation"]["source_current"], False)

    def test_changed_host_environment_requires_new_preparation_before_launch(self):
        request = self.prepare()
        with patch.dict(os.environ, {"DISPLAY": ":different"}):
            with self.assertRaisesRegex(ValueError, "host or admitted environment"):
                self.execute(request)
        self.assertFalse((self.root/".workbench/check-attempts"/request["attempt_id"]/"started.json").exists())

    def test_local_latest_tag_is_not_an_official_release_claim(self):
        subprocess.run(["git", "-C", str(self.pack), "tag", "latest"], check=True)
        request = self.prepare()
        self.assertEqual(request["provenance"]["source_labels"]["local_tags"], ["latest"])
        self.assertFalse(request["provenance"]["source_labels"]["release_verified"])

    def test_live_progress_and_historical_reopen_do_not_require_profile(self):
        request = self.prepare()
        self.assertEqual(self.command("progress", request["attempt_id"])["result"]["state"], "prepared-not-run")
        result = self.execute(request)["result"]
        with patch("workbench_shell.developer_checks.provider", side_effect=ValueError("profile disabled")):
            status = self.command("progress", request["attempt_id"])["result"]
            self.assertEqual(status["state"], "finished")
            self.assertEqual(status["progress"]["observation"]["state"], "checkpoint")
            self.assertEqual(self.command("show", request["attempt_id"])["result"], result)

    def test_runtime_crash_report_is_collected_and_closes_error_screen(self):
        (self.template / "fixture.py").write_text(
            "import pathlib, time\n"
            "pathlib.Path('logs').mkdir()\n"
            "pathlib.Path('logs/latest.log').write_text('startup started\\n')\n"
            "pathlib.Path('logs/groovy.log').write_text('')\n"
            "pathlib.Path('crash-reports').mkdir()\n"
            "pathlib.Path('crash-reports/crash-client.txt').write_text('---- Minecraft Crash Report ----\\nDescription: Initializing game\\nCaused by: InjectionError: test\\n')\n"
            "time.sleep(30)\n"
        )
        self.image = check_storage.import_image(self.root, self.template, Path(sys.executable).resolve(), ["fixture.py"], policy.binding(capture_source_inputs(self.pack)), excluded_roots=policy.descriptor()["excluded_roots"])
        request = self.prepare()
        result = self.execute(request)["result"]
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["execution"]["stop_reason"], "failure-observed")
        self.assertEqual(result["cleanup"]["state"], "trashed")
        self.assertIn("crash-reports/crash-client.txt", {row["path"] for row in result["evidence"]})
        self.assertIn("InjectionError", self.command("log", request["attempt_id"], "--path", "crash-reports/crash-client.txt")["result"]["text"])

    def test_eof_compiler_location_survives_real_supervised_fixture(self):
        script = self.pack / "groovy/postInit/chemistry/Probe.groovy"
        script.write_text("BROKEN (\n")
        (self.template / "fixture.py").write_text(FIXTURE.replace("Probe.groovy: 1:", "Probe.groovy: 2:"))
        self.image = check_storage.import_image(self.root, self.template, Path(sys.executable).resolve(), ["fixture.py"], policy.binding(capture_source_inputs(self.pack)), excluded_roots=policy.descriptor()["excluded_roots"])
        result = self.execute(self.prepare())["result"]
        location = result["interpretation"]["findings"][0]["location"]
        self.assertEqual(location["byte_start"], len(script.read_bytes()))
        self.assertEqual(location["byte_end"], location["byte_start"])
        self.assertEqual(location["start"], {"line": 2, "column": 1})

    def test_killed_writer_requires_explicit_recovery_of_its_owned_child(self):
        (self.template / "fixture.py").write_text(
            "from pathlib import Path\nimport time\n"
            "Path('owned-ready').write_text('ready')\ntime.sleep(60)\n"
        )
        self.image = check_storage.import_image(self.root, self.template, Path(sys.executable).resolve(), ["fixture.py"], policy.binding(capture_source_inputs(self.pack)), excluded_roots=policy.descriptor()["excluded_roots"])
        request = self.command("prepare", "--image", self.image["id"], "--timeout", "60")["result"]
        worker_source = """
import sys
from pathlib import Path
sys.path.insert(0, 'validation')
from run_python_suite import _configure_suite
_configure_suite('developer-feature')
from unittest.mock import patch
from workbench_profile_supersymmetry import developer_checks as policy
from workbench_shell.developer_checks import run_checks
from workbench_shell.developer_context import DeveloperSelection
selection = DeveloperSelection(Path(sys.argv[1]).as_uri(), 'supersymmetry', 'cleanroom', 'cleanroom-provisional')
with patch.object(policy, 'validate_image', return_value=None):
    run_checks(selection, ['execute', sys.argv[3], '--confirm', sys.argv[4]], state_root=Path(sys.argv[2]))
"""
        worker = subprocess.Popen([sys.executable, "-c", worker_source, str(self.pack), str(self.state), request["attempt_id"], request["id"]], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            marker = self.root / ".workbench/tmp" / request["attempt_id"] / "owned-ready"
            deadline = time.monotonic() + 15
            while not marker.exists() and worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "fixture never reached its launch boundary")
            worker.kill()  # Exact test writer only; Core must recover its child.
            worker.wait(timeout=5)
            with patch("workbench_shell.developer_checks.provider", side_effect=ValueError("profile removed")):
                self.assertEqual(self.command("progress", request["attempt_id"])["result"]["state"], "needs-attention")
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    self.command("recover", request["attempt_id"], "--confirm", "wrong")
                recovery = self.command("recover", request["attempt_id"], "--confirm", request["id"])["result"]
                self.assertEqual(recovery["process"]["state"], "closed")
                self.assertEqual(recovery["cleanup"]["state"], "trashed")
                self.assertEqual(self.command("show", request["attempt_id"])["result"]["state"], "recovered-incomplete")
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
            if worker.stderr:
                worker.stderr.close()
            # On an assertion failure, still close only the admitted test child.
            self.command("recover", request["attempt_id"], "--confirm", request["id"])

    def test_stale_source_and_nonmatching_consent_reject_before_execution(self):
        request = self.prepare()
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.command("execute", request["attempt_id"], "--confirm", "wrong")
        (self.pack / "groovy/new.groovy").write_text("// changed\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.execute(request)
        self.assertEqual(
            self.command("show", request["attempt_id"])["result"]["state"],
            "prepared-not-run",
        )

    def test_cancel_before_launch_is_retained_and_not_reexecuted(self):
        request = self.prepare()
        self.command("cancel", request["attempt_id"])
        result = self.execute(request)["result"]
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["execution"]["state"], "not-started")
        with self.assertRaisesRegex(ValueError, "already started"):
            self.execute(request)

    def test_dependency_change_rejects_reuse_of_image(self):
        (self.pack / "pack.toml").write_text(
            (self.pack / "pack.toml").read_text().replace('version = "test"', 'version = "changed-dependency-release"')
        )
        with self.assertRaisesRegex(ValueError, "dependency binding"):
            self.prepare()

    def test_environment_recovery_does_not_require_installed_profile(self):
        from workbench_core.environment_preparation import attempt_path

        identity = "environment-" + "a" * 32
        attempt = attempt_path(self.root, identity)
        attempt.mkdir(parents=True)
        request = check_storage.seal("environment-request", {
            "format": "workbench-environment-request-v1", "state": "planned-not-run",
            "attempt_id": identity, "workspace_uri": self.selection.pack_uri,
            "selection_id": self.selection.id,
        })
        check_storage.write_json(attempt / "request.json", request)
        with patch("workbench_shell.developer_checks.provider", side_effect=AssertionError("profile must not be loaded")):
            self.assertEqual(self.command("environment-show", identity)["result"], request)
            self.command("environment-cancel", identity)
            recovered = self.command("environment-recover", identity, "--confirm", request["id"])["result"]
            self.assertEqual(recovered["state"], "recovered-incomplete")

    def test_runtime_image_and_evidence_tampering_reject(self):
        request = self.prepare()
        self.execute(request)
        attempt = self.root / ".workbench/check-attempts" / request["attempt_id"]
        (attempt / "logs/latest.log").write_text("changed")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.command("show", request["attempt_id"])
        payload = check_storage.image_path(self.root, self.image["id"]) / "payload"
        (payload / "fixture.py").write_text("changed")
        with self.assertRaisesRegex(ValueError, "image bytes"):
            self.prepare()

    def test_projection_failure_retains_result_and_cleanup_state(self):
        request = self.prepare()
        with patch.object(
            check_storage, "provision_projection", side_effect=OSError("disk full")
        ):
            result = self.execute(request)["result"]
        self.assertEqual(result["state"], "failed")
        self.assertIn("disk full", result["error"])
        self.assertEqual(result["execution"]["state"], "not-started")

    def test_staged_source_tampering_rejects(self):
        request = self.prepare()
        path = (
            self.root
            / ".workbench/check-attempts"
            / request["attempt_id"]
            / "source/pack.toml"
        )
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            self.execute(request)

    def test_cleanup_failure_is_separate_and_explicitly_recoverable(self):
        request = self.prepare()
        with patch.object(
            check_storage, "cleanup_projection", side_effect=OSError("busy")
        ):
            result = self.execute(request)["result"]
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["cleanup"]["state"], "blocked")
        recovery = self.command(
            "recover", request["attempt_id"], "--confirm", request["id"]
        )["result"]
        self.assertEqual(recovery["state"], "recovered")
        self.assertEqual(recovery["cleanup"]["state"], "trashed")
        self.assertEqual(self.command("show", request["attempt_id"])["result"], result)

    def test_source_edits_after_launch_do_not_retarget_the_running_candidate(self):
        from workbench_core import check_execution

        real_execute = check_execution.execute
        request = self.prepare()

        def editing_execute(*args, **kwargs):
            (self.pack / "groovy/next-edit.groovy").write_text("// keep editing\n")
            return real_execute(*args, **kwargs)

        with patch.object(check_execution, "execute", side_effect=editing_execute):
            response = self.execute(request)
        self.assertEqual(response["result"]["state"], "completed")
        self.assertFalse(response["presentation"]["source_current"])
        self.assertEqual(response["result"]["candidate_id"], request["candidate"]["id"])
        self.assertEqual(
            (self.pack / "groovy/next-edit.groovy").read_text(), "// keep editing\n"
        )

    def test_timeout_never_becomes_a_pass_and_logs_are_retained(self):
        (self.template / "fixture.py").write_text("import time\ntime.sleep(20)\n")
        self.image = check_storage.import_image(
            self.root,
            self.template,
            Path(sys.executable).resolve(),
            ["fixture.py"],
            policy.binding(capture_source_inputs(self.pack)),
            excluded_roots=policy.descriptor()["excluded_roots"],
        )
        result = self.execute(self.prepare())["result"]
        self.assertEqual(result["state"], "timed-out")
        self.assertEqual(result["execution"]["state"], "closed")
        self.assertTrue(
            any(row["path"] == "logs/process-stdout.raw" for row in result["evidence"])
        )

    def test_concurrent_executor_is_rejected(self):
        request = self.prepare()
        attempt = self.root / ".workbench/check-attempts" / request["attempt_id"]
        with check_storage.execution_lock(attempt):
            with self.assertRaisesRegex(ValueError, "already executing"):
                self.execute(request)

    def test_post_checkpoint_process_failure_is_not_clean(self):
        from workbench_crucible.developer_checks import outcome

        execution = {
            "state": "closed",
            "stop_reason": "process-exited",
            "console": {"effective_exit_code": 1, "cancellation": None},
        }
        self.assertEqual(
            outcome(execution, {"outcome": "completed", "findings_count": 0}, None),
            "failed",
        )

    def test_execution_scrubs_credentials_and_jvm_injection_environment(self):
        (self.template / "fixture.py").write_text(
            "import os\nassert 'WORKBENCH_CHECK_SECRET' not in os.environ\nassert 'JAVA_TOOL_OPTIONS' not in os.environ\nassert os.environ['HOME'] == os.getcwd()\nprint('private-environment-confirmed')\n"
            + FIXTURE
        )
        self.image = check_storage.import_image(
            self.root,
            self.template,
            Path(sys.executable).resolve(),
            ["fixture.py"],
            policy.binding(capture_source_inputs(self.pack)),
            excluded_roots=policy.descriptor()["excluded_roots"],
        )
        with patch.dict(
            os.environ,
            {
                "WORKBENCH_CHECK_SECRET": "fixture-secret",
                "JAVA_TOOL_OPTIONS": "-Dfixture=true",
            },
        ):
            result = self.execute(self.prepare())["result"]
        self.assertEqual(result["state"], "completed")
        text = self.command(
            "log", result["attempt_id"], "--path", "logs/process-stdout.raw"
        )["result"]["text"]
        self.assertIn("private-environment-confirmed", text)

    def test_pack_admission_rejects_extra_mods_and_changed_required_bytes(self):
        from hashlib import sha256

        # Bypass only platform admission for a tiny hash-locked mod fixture.
        platform = type(
            "Platform", (), {"validate_client_image": staticmethod(lambda *args: None)}
        )()
        patcher = patch.object(
            policy, "require_profile_extension", return_value=platform
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        validate = VALIDATE_IMAGE
        mods = self.template / "mods"
        mods.mkdir()
        (mods / "current.jar").write_bytes(b"current")
        (self.pack / "mods/current.pw.toml").write_text(
            'filename = "current.jar"\n[download]\nhash-format = "sha256"\nhash = "'
            + sha256(b"current").hexdigest()
            + '"\n'
        )
        inputs = capture_source_inputs(self.pack)
        validate(inputs, self.template, Path(sys.executable), [])
        (mods / "legacy.jar").write_bytes(b"legacy")
        with self.assertRaisesRegex(ValueError, "undeclared"):
            validate(inputs, self.template, Path(sys.executable), [])
        (mods / "legacy.jar").unlink()
        (mods / "current.jar").write_bytes(b"drift")
        with self.assertRaisesRegex(ValueError, "differs"):
            validate(inputs, self.template, Path(sys.executable), [])

    def test_check_results_link_without_promoting_work_session_execution(self):
        from workbench_shell.developer_context import retain_selection
        from workbench_shell.work_session import WorkSessionStore
        from test_developer_context import FRONTEND

        selected = retain_selection(self.selection, self.state, frontend=FRONTEND)
        store = WorkSessionStore(self.state)
        result = self.execute(self.prepare())
        session = selected["session_id"]
        store.bind_owner_artifacts(
            session,
            expected_sequence=store.status(session)["latest_sequence"],
            frontend=FRONTEND,
            owner_record_refs=[result["owner_record_ref"]],
            owner_reference_verifier=lambda row: verify_developer_owner_reference(
                row, self.selection, suite_root=ROOT
            ),
        )
        reopened = store.open(session)["summary"]
        self.assertNotIn(reopened["lifecycle"], {"running", "complete"})
        self.assertEqual(
            reopened["owner_record_refs"][0]["last_verified_state"], "completed"
        )

    def test_owner_cancellation_closes_an_already_launched_process(self):
        request = self.prepare()
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 2

        result = run_checks(
            self.selection,
            ["execute", request["attempt_id"], "--confirm", request["id"]],
            state_root=self.state,
            cancelled=cancelled,
        )["result"]
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["execution"]["state"], "closed")
        self.assertEqual(result["cleanup"]["state"], "trashed")

    def test_checkpoint_shutdown_does_not_hide_observed_or_process_failures(self):
        from workbench_crucible.developer_checks import outcome

        interpretation = {"outcome": "completed", "findings_count": 0}
        for console in (
            {"process_exit_code": -2, "outcome_failure_events": 1},
            {"process_exit_code": 1, "outcome_failure_events": 0},
        ):
            execution = {
                "state": "closed",
                "stop_reason": "checkpoint-reached",
                "console": {
                    **console,
                    "effective_exit_code": 130,
                    "cancellation": "interrupt-requested",
                },
            }
            self.assertEqual(outcome(execution, interpretation, None), "failed")
