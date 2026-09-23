"""IDE qualification preserves full hosts, locked tools and honest failures."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation"))
import validate_ide as ide
from validation_diagnostics import DiagnosticRun


class IdeValidationTests(unittest.TestCase):
    def test_failed_client_cancels_the_other_owned_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "ready"
            def slow():
                script = "import pathlib,time; pathlib.Path(" + repr(str(ready)) + ").write_text('ready'); time.sleep(60)"
                ide.current_diagnostics().command([sys.executable, "-c", script], cwd=root)
            def failed():
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if time.monotonic() > deadline:
                        raise AssertionError("other client never started")
                    time.sleep(.01)
                raise RuntimeError("client contract failed")
            with self.assertRaisesRegex(RuntimeError, "client contract failed"):
                with DiagnosticRun(root / "reports", "ide", ("vscode", "intellij")) as diagnostics:
                    ide.run_clients(diagnostics, {"vscode": slow, "intellij": failed}, jobs=2)
            report = json.loads((root / "reports/vscode/report.json").read_text())
            self.assertEqual("cancelled", report["state"])
            self.assertIsNotNone(report["phases"][-1]["exit_code"])

    def test_parallel_clients_overlap_with_separate_diagnostics_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environments = [ide.client_environment(root / name, {}) for name in ("a", "b")]
            self.assertNotEqual(environments[0]["WORKBENCH_CONFIG_HOME"], environments[1]["WORKBENCH_CONFIG_HOME"])
            barrier = threading.Barrier(2)
            def check():
                barrier.wait(timeout=5)
                ide.current_diagnostics().command([sys.executable, "-c", "print('client checked')"], cwd=root)
            with DiagnosticRun(root / "reports", "ide", ("vscode", "intellij")) as diagnostics:
                ide.run_clients(diagnostics, {"vscode": check, "intellij": check}, jobs=2)
            report = json.loads((root / "reports/report.json").read_text())
            self.assertEqual("passed", report["state"])
            for name in ("vscode", "intellij"):
                child = json.loads((root / "reports" / name / "report.json").read_text())
                self.assertEqual("passed", child["state"])
                self.assertEqual("checks", child["phases"][-1]["parent"])

    def test_locked_node_and_npm_are_used_for_every_vscode_step(self):
        lock = {"node": {"version": "22.0.0"}, "npm": {"version": "11.0.0"}}
        commands = []
        with patch.object(ide, "capture", side_effect=["v22.0.0\n", "11.0.0\n"]), patch.object(ide, "run", side_effect=lambda command, **kwargs: commands.append(command)), patch.object(ide, "run_vscode_extension_host") as host:
            ide.validate_vscode(lock, full=True, non_adversarial=False, environment={"PATH": "/locked"}, node=Path("/locked/node"), npm_cli=Path("/locked/npm-cli.js"), installed_core=Path("/installed/workbench"))
        self.assertEqual(["ci", "test", "run"], [command[2] for command in commands])
        self.assertTrue(all(command[:2] == ["/locked/node", "/locked/npm-cli.js"] for command in commands))
        self.assertEqual(Path("/installed/workbench"), host.call_args.kwargs["installed_core"])

    def test_non_adversarial_lane_still_checks_install_and_package_but_not_hosts(self):
        lock = {"node": {"version": "22"}, "npm": {"version": "11"}}
        with patch.object(ide, "require_version"), patch.object(ide, "run") as run, patch.object(ide, "run_vscode_extension_host") as host:
            ide.validate_vscode(lock, full=True, non_adversarial=True)
        self.assertEqual(["ci", "run"], [call.args[0][1] for call in run.call_args_list])
        host.assert_not_called()

    def test_failed_provision_retains_later_stages_as_unrun(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "ide"
            with patch.object(ide, "provision", side_effect=ide.ProvisionFailure("required tool absent")):
                with self.assertRaises(ide.ProvisionFailure):
                    ide.main(["--full", "--diagnostics", str(directory)])
            report = json.loads((directory / "report.json").read_text())
            self.assertEqual("failed", report["state"])
            self.assertEqual(["failed", "unrun", "unrun"], [row["state"] for row in report["phases"]])
            self.assertIsNone(ide.DIAGNOSTICS)

    def test_installed_journey_cannot_be_requested_in_a_skipped_host_lane(self):
        for arguments in (["--installed-core", "/core"], ["--full", "--non-adversarial", "--installed-core", "/core"], ["--full", "--shared-workspace", "/workspace"]):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                ide.main(arguments)
