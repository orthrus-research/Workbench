"""Native-host tests for the process port used by developer recipe captures."""

from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from workbench_api.processes import ProcessError
from workbench_core import process_capture, tool_process
from workbench_core.host_filesystem import private_path, secure_private_path


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            status = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
                raise ctypes.WinError(ctypes.get_last_error())
            return status.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path("/proc") / str(pid) / "stat"
    return not status.exists() or status.read_text().rsplit(")", 1)[-1].split()[0] != "Z"


class PortableToolProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-process-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "capture é"
        secure_private_path(self.root, directory=True)

    def capture(self, script, **overrides):
        values = dict(
            directory=self.root / "output", binding="portable-process:fixture",
            cwd=self.root, stdin=b"", environment={}, cancelled=threading.Event(),
            timeout_seconds=5, output_limit=None,
        )
        values.update(overrides)
        return tool_process.capture([sys.executable, "-c", script], **values)

    def assert_stopped(self, *pids):
        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse([pid for pid in pids if alive(pid)], "owned process survived closure")

    def descendants(self, *, leave=False):
        child_marker, parent_marker = self.root / "child.pid", self.root / "parent.pid"
        child = (
            "import os,time\nfrom pathlib import Path\n"
            f"Path({str(child_marker)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        parent = (
            "import os,sys,subprocess,time\nfrom pathlib import Path\n"
            f"Path({str(parent_marker)!r}).write_text(str(os.getpid()))\n"
            f"subprocess.Popen([sys.executable,'-c',{child!r}])\n"
            f"while not Path({str(child_marker)!r}).exists(): time.sleep(.01)\n"
            + ("sys.exit(0)\n" if leave else "time.sleep(60)\n")
        )
        return parent, parent_marker, child_marker

    def test_binary_input_and_concurrent_output_keep_exact_bytes_and_status(self):
        payload = bytes(range(256)) * 8192
        script = """import sys,threading
data=sys.stdin.buffer.read()
threads=[threading.Thread(target=lambda stream=stream, payload=payload:
    (stream.write(payload),stream.flush())) for stream,payload in
    [(sys.stdout.buffer,data),(sys.stderr.buffer,bytes(range(255,-1,-1))*8192)]]
for thread in threads: thread.start()
for thread in threads: thread.join()
sys.exit(7)
"""
        result = self.capture(script, stdin=payload)
        self.assertEqual(7, result.exit_code)
        for output, expected in ((result.stdout, payload),
                                 (result.stderr, bytes(range(255, -1, -1)) * 8192)):
            self.assertEqual((len(expected), sha256(expected).hexdigest()),
                             (output.size, output.sha256))
            with tool_process.open_output(output) as stream:
                self.assertEqual(expected, stream.read())
        self.assertTrue(private_path(self.root / "output", directory=True))
        for path in (self.root / "output").iterdir():
            self.assertTrue(private_path(path, directory=False), path)

    def test_timeout_keeps_incomplete_evidence(self):
        with self.assertRaisesRegex(ProcessError, "timed out"):
            self.capture("import time; time.sleep(60)", timeout_seconds=.2)
        result = process_capture.load(self.root / "output", binding="portable-process:fixture")
        self.assertEqual("incomplete", result["state"])

    def test_cancel_terminates_target_and_its_descendants(self):
        script, parent_marker, child_marker = self.descendants()

        class CancelWhenStarted:
            def is_set(self):
                return child_marker.exists() and child_marker.read_text().isdigit()

        with self.assertRaisesRegex(ProcessError, "cancelled"):
            self.capture(script, cancelled=CancelWhenStarted(), timeout_seconds=None)
        self.assert_stopped(int(parent_marker.read_text()), int(child_marker.read_text()))
        result = process_capture.load(self.root / "output", binding="portable-process:fixture")
        self.assertEqual("incomplete", result["state"])

    def test_exited_target_cannot_leave_a_descendant_holding_output_open(self):
        script, parent_marker, child_marker = self.descendants(leave=True)
        with self.assertRaisesRegex(ProcessError, "cancelled"):
            self.capture(script, timeout_seconds=5)
        self.assert_stopped(int(parent_marker.read_text()), int(child_marker.read_text()))

    def test_pre_cancel_does_not_create_capture(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ProcessError, "before launch"):
            self.capture("raise AssertionError()", cancelled=cancelled)
        self.assertFalse((self.root / "output").exists())

    def test_output_bound_closes_process_and_keeps_incomplete_evidence(self):
        marker = self.root / "output.pid"
        script = (
            "import os,sys,time\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
            "sys.stdout.buffer.write(b'x'*65536); sys.stdout.buffer.flush()\n"
            "time.sleep(60)\n"
        )
        with self.assertRaisesRegex(ProcessError, "byte bound"):
            self.capture(script, output_limit=32)
        self.assert_stopped(int(marker.read_text()))
        result = process_capture.load(self.root / "output", binding="portable-process:fixture")
        self.assertEqual("incomplete", result["state"])

    def test_same_size_retained_output_change_is_rejected(self):
        result = self.capture("import sys; sys.stdout.buffer.write(b'value')")
        result.stdout.path.write_bytes(b"other")
        with self.assertRaisesRegex(ProcessError, "changed while reading"):
            with tool_process.open_output(result.stdout) as stream:
                stream.read()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object custody")
    def test_job_assignment_failure_does_not_release_gate(self):
        marker = self.root / "must-not-execute"
        with patch("workbench_core.windows_process.ProcessJob", side_effect=OSError("job refused")):
            with self.assertRaisesRegex(ProcessError, "job refused"):
                self.capture(f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')")
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows kill-on-close job custody")
    def test_supervisor_death_closes_entire_owned_job(self):
        script, parent_marker, child_marker = self.descendants()
        import workbench_api
        import workbench_core

        source_roots = [str(Path(package.__file__).parent.parent)
                        for package in (workbench_api, workbench_core)]
        supervisor = (
            f"import sys; sys.path[:0]={source_roots!r}\n"
            "from pathlib import Path\nimport threading\n"
            "from workbench_core import tool_process\n"
            f"tool_process.execute([sys.executable,'-c',{script!r}],"
            f"cwd=Path({str(self.root)!r}),stdin=b'',environment={{}},"
            "cancelled=threading.Event(),timeout_seconds=None,output_limit=None)\n"
        )
        process = subprocess.Popen([sys.executable, "-c", supervisor],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not child_marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(child_marker.exists(), "supervisor did not launch its target")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.assert_stopped(int(parent_marker.read_text()), int(child_marker.read_text()))


if __name__ == "__main__":
    unittest.main()
