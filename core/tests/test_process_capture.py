from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from workbench_api.processes import ProcessError
from workbench_core import check_storage as storage, process_capture, tool_process


@unittest.skipUnless(sys.platform == "linux", "Linux process lifecycle qualification")
class ProcessCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "capture"

    def capture(self, script, **overrides):
        values = dict(directory=self.directory, binding="request:fixture", cwd=self.root, stdin=b"",
                      environment={}, cancelled=threading.Event(), timeout_seconds=2, output_limit=None)
        values.update(overrides)
        return tool_process.capture([sys.executable, "-c", script], **values)

    def test_complete_binary_streams_are_durable_without_byte_capture(self):
        payload = bytes(range(256)) * 8192
        with patch.object(tool_process, "_Capture", side_effect=AssertionError("byte buffers must not be allocated")):
            result = self.capture("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); "
                                  "sys.stderr.buffer.write(bytes(range(256))); sys.exit(4)", stdin=payload)
        self.assertEqual(4, result.exit_code)
        self.assertEqual(len(payload), result.stdout.size)
        self.assertEqual(sha256(payload).hexdigest(), result.stdout.sha256)
        with tool_process.open_output(result.stdout) as stream:
            self.assertEqual(payload, stream.read())
        with tool_process.open_output(result.stderr) as stream:
            self.assertEqual(bytes(range(256)), stream.read())
        record = process_capture.load(self.directory, binding=result.binding, expected_id=result.capture_id)
        self.assertEqual(result, process_capture.result(self.directory, record))
        self.assertEqual(0o700, self.directory.stat().st_mode & 0o777)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.directory.iterdir()))

    def test_concurrent_large_stdout_and_stderr_are_complete(self):
        script = """import sys, threading
def write(stream, byte):
    for _ in range(128): stream.write(byte * 65536)
    stream.flush()
threads = [threading.Thread(target=write, args=pair) for pair in
           [(sys.stdout.buffer, b'a'), (sys.stderr.buffer, b'b')]]
for thread in threads: thread.start()
for thread in threads: thread.join()
"""
        result = self.capture(script)
        for output, byte in [(result.stdout, b"a"), (result.stderr, b"b")]:
            self.assertEqual(8388608, output.size)
            with tool_process.open_output(output) as stream:
                self.assertEqual(byte * 8388608, stream.read())

    def test_existing_destination_and_nonprivate_parent_refuse_before_launch(self):
        self.directory.mkdir()
        sentinel = self.directory / "sentinel"
        sentinel.write_bytes(b"preserved")
        with self.assertRaises(FileExistsError):
            self.capture("raise AssertionError('not launched')")
        self.assertEqual(b"preserved", sentinel.read_bytes())
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ProcessError, "owner-private"):
            self.capture("raise AssertionError('not launched')", directory=self.root / "other")
        self.assertFalse((self.root / "other").exists())

    def test_symlink_parent_cannot_capture(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.capture("print('not launched')", directory=alias / "capture")

    def test_cancellation_closes_child_and_retains_explicit_partial_evidence(self):
        marker = self.root / "pid"
        class CancelAfterOutput:
            def is_set(self):
                try: return marker.read_text().isdigit()
                except FileNotFoundError: return False
        script = ("import os,sys\nfrom pathlib import Path\n"
                  "sys.stdout.buffer.write(b'x' * 65536); sys.stdout.buffer.flush()\n"
                  f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                  "while True: sys.stdout.buffer.write(b'x' * 65536); sys.stdout.buffer.flush()\n")
        with self.assertRaisesRegex(ProcessError, "cancelled"):
            self.capture(script, cancelled=CancelAfterOutput(), timeout_seconds=None)
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()), 0)
        record = process_capture.load(self.directory, binding="request:fixture")
        self.assertEqual("incomplete", record["state"])
        self.assertGreater(record["streams"]["stdout"]["bytes"], 0)
        with self.assertRaisesRegex(ProcessError, "incomplete"):
            process_capture.result(self.directory, record)
        self.assertEqual(4, len(process_capture.retained_files(self.directory, binding="request:fixture")))

    def test_cancelled_before_launch_allocates_no_capture(self):
        token = threading.Event(); token.set()
        with self.assertRaisesRegex(ProcessError, "cancelled"):
            self.capture("print('not launched')", cancelled=token)
        self.assertFalse(self.directory.exists())

    def test_timeout_and_output_bound_never_return_complete_reference(self):
        for name, script, options in [("timeout", "import time; time.sleep(20)", {"timeout_seconds": .1}),
                                      ("bound", "print('x' * 10000)", {"output_limit": 128})]:
            directory = self.root / name
            with self.subTest(name=name), self.assertRaises(ProcessError):
                self.capture(script, directory=directory, **options)
            self.assertEqual("incomplete", process_capture.load(directory, binding="request:fixture")["state"])

    def test_disk_write_failure_is_not_successful_capture(self):
        original = process_capture.FileCapture.write_raw
        def fail(session, stream, data):
            if stream == "stdout" and data: raise OSError("fixture storage full")
            return original(session, stream, data)
        with patch.object(process_capture.FileCapture, "write_raw", fail), self.assertRaisesRegex(ProcessError, "storage full"):
            self.capture("import sys,time; print('payload', flush=True); time.sleep(20)")
        self.assertEqual("incomplete", process_capture.load(self.directory, binding="request:fixture")["state"])

    def test_uncommitted_interrupted_capture_remains_owned_but_not_complete(self):
        session = process_capture.FileCapture(self.directory, "request:fixture", None)
        session.write_raw("stdout", b"partial")
        session.stack.close()  # Interrupted publication: no completion manifest.
        self.assertEqual(3, len(process_capture.retained_files(self.directory, binding="request:fixture")))
        with self.assertRaisesRegex(ProcessError, "not committed"):
            process_capture.retained_files(self.directory, binding="request:fixture", expected_id="missing")

    def test_wrong_owner_and_changed_capture_identity_refuse(self):
        result = self.capture("print('value')")
        with self.assertRaisesRegex(ProcessError, "another owner"):
            process_capture.load(self.directory, binding="another-request")
        with self.assertRaisesRegex(ProcessError, "metadata changed"):
            process_capture.load(self.directory, binding=result.binding, expected_id="different-id")

    def test_same_size_corruption_truncation_and_changed_digest_refuse(self):
        result = self.capture("import sys; sys.stdout.write('value')")
        result.stdout.path.write_bytes(b"other")
        with self.assertRaisesRegex(ProcessError, "changed while reading"), tool_process.open_output(result.stdout) as stream:
            stream.read()
        result.stdout.path.write_bytes(b"val")
        with self.assertRaisesRegex(ProcessError, "changed while reading"), tool_process.open_output(result.stdout):
            pass
        result.stdout.path.write_bytes(b"value")
        with self.assertRaisesRegex(ProcessError, "changed while reading"), tool_process.open_output(replace(result.stdout, sha256="0" * 64)):
            pass

    def test_file_replacement_during_read_refuses_even_with_identical_bytes(self):
        result = self.capture("print('value')")
        with self.assertRaisesRegex(ProcessError, "changed while reading"), tool_process.open_output(result.stdout) as stream:
            raw = stream.read()
            result.stdout.path.unlink()
            result.stdout.path.write_bytes(raw)

    def test_output_links_are_rejected(self):
        result = self.capture("print('value')")
        alias = self.root / "alias"
        alias.symlink_to(result.stdout.path)
        with self.assertRaisesRegex(ValueError, "symbolic link"), tool_process.open_output(replace(result.stdout, path=alias)):
            pass
        alias.unlink()
        os.link(result.stdout.path, alias)
        with self.assertRaisesRegex(ValueError, "ordinary independent"), tool_process.open_output(result.stdout):
            pass
