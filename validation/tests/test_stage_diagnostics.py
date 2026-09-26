"""Subprocess diagnostics remain bounded and fail closed after interruption."""
import json
import os
import shutil
import stat
from contextlib import redirect_stdout
from io import BytesIO, TextIOWrapper
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from validation_diagnostics import DiagnosticRun, OUTPUT_LIMIT, load_diagnostic_report


class StageDiagnosticsTests(unittest.TestCase):
    def test_unicode_command_survives_legacy_console_encoding(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            arguments = [sys.executable, "-c", "import sys; print(ascii(sys.argv[1]))", "資料 😀"]
            captured = BytesIO()
            console = TextIOWrapper(captured, encoding="cp1252", errors="strict")
            with redirect_stdout(console), DiagnosticRun(output, "unicode") as run:
                result = run.command(arguments, cwd=temporary)
            console.flush()
            self.assertIn(b"\\u8cc7", captured.getvalue())
            self.assertEqual(ascii(arguments[-1]), result.stdout.strip())
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(arguments, report["phases"][0]["command"])
            self.assertEqual("passed", report["state"])

    def test_expected_rejection_and_bounded_output_are_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            with DiagnosticRun(output, "probe", ("check",)) as run:
                with run.phase("check"):
                    result = run.command([sys.executable, "-c", f"import sys; print('x'*{OUTPUT_LIMIT + 100}); sys.exit(2)"], cwd=temporary, expected=2)
                    self.assertEqual(2, result.returncode)
                    self.assertLessEqual(len(result.stdout), OUTPUT_LIMIT)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual("passed", report["state"])
            command = report["phases"][-1]
            self.assertTrue(command["output_truncated"]["stdout"])
            self.assertEqual("check", command["parent"])
            self.assertLessEqual((output / command["stdout"]).stat().st_size, OUTPUT_LIMIT)

    def test_failure_keeps_logs_and_marks_later_work_unrun(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            with self.assertRaises(subprocess.CalledProcessError):
                with DiagnosticRun(output, "probe", ("first", "later")) as run:
                    with run.phase("first"):
                        run.command([sys.executable, "-c", "import sys; print('specific failure', file=sys.stderr); sys.exit(3)"], cwd=temporary)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual("failed", report["state"])
            self.assertEqual("unrun", report["phases"][1]["state"])
            self.assertIn("specific failure", (output / report["phases"][-1]["stderr"]).read_text())

    def test_timeout_and_keyboard_interrupt_are_distinct_terminal_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            timeout_dir = Path(temporary) / "timeout"
            with self.assertRaises(subprocess.TimeoutExpired):
                with DiagnosticRun(timeout_dir, "probe") as run:
                    run.command([sys.executable, "-c", "import time; time.sleep(60)"], cwd=temporary, timeout=0.05)
            report = json.loads((timeout_dir / "report.json").read_text())
            self.assertEqual("timed-out", report["phases"][0]["state"])
            cancelled = Path(temporary) / "cancelled"
            with self.assertRaises(KeyboardInterrupt):
                with DiagnosticRun(cancelled, "probe", ("first", "later")) as run:
                    with run.phase("first"):
                        raise KeyboardInterrupt()
            self.assertEqual("cancelled", json.loads((cancelled / "report.json").read_text())["state"])

    def test_missing_phase_or_reused_directory_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            with self.assertRaisesRegex(RuntimeError, "every required"):
                with DiagnosticRun(directory, "probe", ("never-ran",)):
                    pass
            original = (directory / "report.json").read_bytes()
            with self.assertRaises(FileExistsError):
                DiagnosticRun(directory, "probe")
            self.assertEqual(original, (directory / "report.json").read_bytes())

    def test_changed_report_refuses_revision_and_retains_external_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = DiagnosticRun(Path(temporary) / "run", "probe")
            report = run.directory / "report.json"
            if os.name != "nt":
                self.assertEqual(0o700, stat.S_IMODE(run.directory.stat().st_mode))
                self.assertEqual(0o600, stat.S_IMODE(report.stat().st_mode))
            changed = report.read_bytes().replace(b'"running"', b'"failed"')
            report.write_bytes(changed)
            from workbench_api.host_filesystem import DurableRecordError
            with self.assertRaises(DurableRecordError) as rejected:
                run._write()
            self.assertEqual("stale", rejected.exception.code)
            self.assertEqual(changed, report.read_bytes())
            self.assertEqual([], list(run.directory.glob("*.tmp")))

    def test_historical_nonprivate_report_remains_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "historical"
            directory.mkdir()
            report = directory / "report.json"
            report.write_text(json.dumps({"format": "workbench-stage-diagnostics-v1", "run_id": "old",
                                          "lane": "probe", "state": "passed", "phases": []}))
            if os.name != "nt":
                directory.chmod(0o755)
                report.chmod(0o644)
            self.assertEqual("passed", load_diagnostic_report(report)["state"])

    def test_source_only_diagnostics_bind_core_without_site_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = ("from pathlib import Path\n"
                      "from validation_diagnostics import DiagnosticRun, load_diagnostic_report\n"
                      "with DiagnosticRun(Path('run'), 'source') as run:\n"
                      "    pass\n"
                      "assert load_diagnostic_report(Path('run/report.json'))['state'] == 'passed'\n")
            environment = dict(os.environ, PYTHONPATH=str(ROOT / "tools"))
            result = subprocess.run([sys.executable, "-S", "-c", script], cwd=temporary,
                                    env=environment, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_diagnostics_tool_uses_installed_core_when_source_tree_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary) / "tools"
            tools.mkdir()
            shutil.copy2(ROOT / "tools/validation_diagnostics.py", tools / "validation_diagnostics.py")
            script = ("from pathlib import Path\n"
                      "from validation_diagnostics import DiagnosticRun, load_diagnostic_report\n"
                      "with DiagnosticRun(Path('run'), 'installed'):\n"
                      "    pass\n"
                      "assert load_diagnostic_report(Path('run/report.json'))['state'] == 'passed'\n")
            environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
                (str(tools), str(ROOT / "api/src"), str(ROOT / "core/src"))))
            result = subprocess.run([sys.executable, "-S", "-c", script], cwd=temporary,
                                    env=environment, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
