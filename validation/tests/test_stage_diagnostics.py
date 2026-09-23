"""Subprocess diagnostics remain bounded and fail closed after interruption."""
import json
from contextlib import redirect_stdout
from io import BytesIO, TextIOWrapper
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from validation_diagnostics import DiagnosticRun, OUTPUT_LIMIT


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
