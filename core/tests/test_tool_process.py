from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest

from workbench_api.processes import ProcessError
from workbench_core import tool_process


@unittest.skipUnless(sys.platform == "linux", "Linux process lifecycle qualification")
class NativeToolProcessTests(unittest.TestCase):
    def execute(self, script, **overrides):
        values = dict(cwd=Path.cwd(), stdin=b"hello", environment={}, cancelled=threading.Event(), timeout_seconds=2, output_limit=1024)
        values.update(overrides)
        return tool_process.execute([sys.executable, "-c", script], **values)

    def test_exact_bytes_and_native_nonzero_status(self):
        result = self.execute("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.write('note'); sys.exit(3)")
        self.assertEqual((3, b"hello", b"note"), (result.exit_code, result.stdout, result.stderr))

    def test_no_ambient_credentials(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"AXIOM_TEST_SECRET": "not-in-child"}):
            result = self.execute("import os; print(os.getenv('AXIOM_TEST_SECRET', 'absent'))")
        self.assertEqual(b"absent\n", result.stdout)

    def test_timeout_closes_process(self):
        with self.assertRaisesRegex(ProcessError, "timed out"):
            self.execute("import time; time.sleep(20)", timeout_seconds=0.1)

    def test_output_bound_fails_without_retaining_unbounded_data(self):
        with self.assertRaisesRegex(ProcessError, "byte bound"):
            self.execute("print('x' * 10000)", output_limit=128)

    def test_cancelled_before_launch(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ProcessError, "cancelled"):
            self.execute("raise RuntimeError('must not run')", cancelled=cancelled)

    def test_suspended_targets_retain_complete_stdout_and_stderr(self):
        result = self.execute("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.write('é' * 700000)",
                              stdin=b"x" * 1200000, timeout_seconds=None, output_limit=None)
        self.assertEqual(b"x" * 1200000, result.stdout)
        self.assertEqual(("é" * 700000).encode(), result.stderr)

    def test_binary_protocol_round_trips_without_text_interpretation(self):
        payload = bytes(range(256)) * 4096
        result = self.execute("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(4)",
                              stdin=payload, timeout_seconds=None, output_limit=None)
        self.assertEqual((result.exit_code, result.stdout, result.stderr), (4, payload, b""))

    def test_both_large_streams_are_drained_without_pipe_deadlock(self):
        script = """import sys, threading
def send(stream, payload):
    for _ in range(64):
        stream.write(payload)
    stream.flush()
threads = [threading.Thread(target=send, args=(stream, payload)) for stream, payload in
           [(sys.stdout.buffer, b'a' * 65536), (sys.stderr.buffer, b'b' * 65536)]]
for thread in threads: thread.start()
for thread in threads: thread.join()
"""
        result = self.execute(script, timeout_seconds=None, output_limit=None)
        self.assertEqual(result.stdout, b"a" * 4194304)
        self.assertEqual(result.stderr, b"b" * 4194304)

    def test_cancellation_during_continuous_output_closes_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "pid"
            class CancelAfterOutput:
                def is_set(self):
                    try:
                        return marker.read_text().isdigit()
                    except FileNotFoundError:
                        return False

            script = ("import os,sys\nfrom pathlib import Path\n"
                      "sys.stdout.buffer.write(b'x' * 65536)\nsys.stdout.buffer.flush()\n"
                      f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                      "while True: sys.stdout.buffer.write(b'x' * 65536); sys.stdout.buffer.flush()\n")
            with self.assertRaisesRegex(ProcessError, "cancelled"):
                self.execute(script, cancelled=CancelAfterOutput(), timeout_seconds=None, output_limit=None)
            pid = int(marker.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_cancellation_without_deadline_closes_the_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        try:
            with self.assertRaisesRegex(ProcessError, "cancelled"):
                self.execute("import time; time.sleep(20)", cancelled=cancelled, timeout_seconds=None, output_limit=None)
        finally:
            timer.cancel()
