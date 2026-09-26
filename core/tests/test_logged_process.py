"""Core owns the historical log while supervising native tools."""

from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest

from workbench_api.processes import ProcessError
from workbench_core import tool_process


class LoggedProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.logs = self.root / "evidence"
        self.logs.mkdir()
        self.path = self.logs / "packwiz-refresh.log"

    def run_tool(self, script: str, *, timeout: float = 2, limit: int = 1024):
        return tool_process.execute_logged(
            [sys.executable, "-c", script], cwd=self.root,
            log_path=self.path, environment=dict(os.environ),
            cancelled=Event(), timeout_seconds=timeout, output_limit=limit,
        )

    def test_exact_log_and_previous_history_are_retained(self) -> None:
        first = self.run_tool("print('first output')")
        self.assertEqual(0, first.exit_code)
        original = self.path.read_bytes()
        self.assertIn(b'"command":', original)
        self.assertIn(b"first output\n", original)
        self.assertEqual(sha256(original).hexdigest(), first.log_sha256)
        self.assertEqual(len(original), first.log_size)
        self.assertEqual(0o700, self.logs.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

        second = self.run_tool("import sys; print('second'); print('warning', file=sys.stderr)")
        self.assertEqual(0, second.exit_code)
        self.assertIn(b"second\n", self.path.read_bytes())
        self.assertIn(b"warning\n", self.path.read_bytes())
        archived = self.logs / "history" / f"packwiz-refresh-{first.log_sha256[:16]}.log"
        self.assertEqual(original, archived.read_bytes())
        self.assertFalse(list(self.logs.glob("*.pending")))

    def test_nonzero_and_timeout_retain_historical_log(self) -> None:
        result = self.run_tool("import sys; print('failure'); sys.exit(7)")
        self.assertEqual(7, result.exit_code)
        with self.assertRaisesRegex(ProcessError, "timed out"):
            self.run_tool("import time; print('before wait', flush=True); time.sleep(20)", timeout=0.1)
        self.assertIn(b"before wait\n", self.path.read_bytes())
        self.assertEqual(1, len(list((self.logs / "history").iterdir())))

    def test_old_owner_log_and_archive_reopen_at_historical_names(self) -> None:
        old = b'{"command": ["old"], "cwd": "/tmp"}\nold output\n'
        self.path.write_bytes(old)
        self.path.chmod(0o644)
        history = self.logs / "history"
        history.mkdir(mode=0o755)
        archived = history / f"packwiz-refresh-{sha256(old).hexdigest()[:16]}.log"
        archived.write_bytes(old)
        archived.chmod(0o644)
        result = self.run_tool("print('new output')")
        self.assertEqual(0, result.exit_code)
        self.assertEqual(old, archived.read_bytes())
        self.assertEqual(0o600, archived.stat().st_mode & 0o777)
        self.assertEqual(0o700, history.stat().st_mode & 0o777)

    def test_output_bound_closes_child_and_keeps_bounded_failure_log(self) -> None:
        with self.assertRaisesRegex(ProcessError, "byte bound"):
            self.run_tool("print('x' * 10000)", limit=256)
        self.assertLessEqual(self.path.stat().st_size, 256)
        self.assertIn(b'"command":', self.path.read_bytes())

    def test_changed_target_and_interrupted_stage_refuse_before_launch(self) -> None:
        other = self.root / "other.log"
        other.write_bytes(b"outside")
        self.path.symlink_to(other)
        with self.assertRaises((OSError, ValueError, ProcessError)):
            self.run_tool("raise RuntimeError('must not run')")
        self.assertEqual(b"outside", other.read_bytes())
        self.path.unlink()
        pending = self.logs / ".packwiz-refresh.log.crashed.pending"
        pending.write_bytes(b"partial")
        with self.assertRaisesRegex(ProcessError, "unpublished pending log"):
            self.run_tool("raise RuntimeError('must not run')")
        self.assertEqual(b"partial", pending.read_bytes())


if __name__ == "__main__":
    unittest.main()
