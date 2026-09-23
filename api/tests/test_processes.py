from pathlib import Path
from threading import Event
import unittest
from unittest.mock import Mock, patch

from workbench_api import processes


class ProcessPortTests(unittest.TestCase):
    def test_file_capture_and_reads_require_explicit_host_support(self):
        arguments = dict(directory=Path('/owned/capture'), binding='request', cwd=Path.cwd(),
                         stdin=b'', environment={}, cancelled=Event())
        with patch.object(processes, '_host', None), self.assertRaises(processes.ProcessError):
            processes.capture_process(['/bin/true'], **arguments)
        host = type('ByteOnlyHost', (), {'execute': lambda *args, **kwargs: None})()
        with patch.object(processes, '_host', host):
            with self.assertRaisesRegex(processes.ProcessError, 'file capture'):
                processes.capture_process(['/bin/true'], **arguments)
            with self.assertRaisesRegex(processes.ProcessError, 'output reads'):
                processes.open_process_output(None)

    def test_file_capture_preserves_owner_binding_and_selected_policy(self):
        host = Mock()
        token = Event()
        with patch.object(processes, '_host', host):
            result = processes.capture_process(['/bin/true'], directory=Path('/owned/capture'), binding='request',
                cwd=Path.cwd(), stdin=b'payload', environment={'LANG': 'C.UTF-8'}, cancelled=token,
                timeout_seconds=None, output_limit=None, input_limit=None)
            self.assertIs(result, host.capture.return_value)
            self.assertEqual('request', host.capture.call_args.kwargs['binding'])
            self.assertIsNone(host.capture.call_args.kwargs['output_limit'])
            token.set()
            with self.assertRaises(processes.ProcessError):
                processes.capture_process(['/bin/true'], directory=Path('/owned/capture'), binding='request',
                    cwd=Path.cwd(), stdin=b'', environment={}, cancelled=token)
            host.capture.assert_called_once()

    def invoke(self, **overrides):
        values = dict(cwd=Path.cwd(), stdin=b"{}", environment={}, cancelled=Event())
        values.update(overrides)
        return processes.execute_process(("/bin/true",), **values)

    def test_requires_explicit_host(self):
        with patch.object(processes, "_host", None), self.assertRaises(processes.ProcessError):
            self.invoke()

    def test_binding_cannot_replace_host(self):
        with patch.object(processes, "_host", None):
            host = Mock()
            processes.bind_process_host(host)
            processes.bind_process_host(host)
            with self.assertRaises(processes.ProcessError):
                processes.bind_process_host(Mock())

    def test_bounds_and_cancellation_precede_invocation(self):
        with patch.object(processes, "_host", Mock()) as host:
            for values in ({"stdin": b"x" * (1024 * 1024 + 1)}, {"timeout_seconds": 0}, {"output_limit": 0}):
                with self.assertRaises(processes.ProcessError):
                    self.invoke(**values)
            cancelled = Event()
            cancelled.set()
            with self.assertRaises(processes.ProcessError):
                self.invoke(cancelled=cancelled)
            host.execute.assert_not_called()

    def test_preserves_exact_result(self):
        result = processes.ProcessResult(3, b'{"status":"unsupported"}', b"")
        with patch.object(processes, "_host", Mock(execute=Mock(return_value=result))):
            self.assertIs(result, self.invoke())

    def test_explicitly_suspended_targets_forward_complete_input_and_cancellation(self):
        cancelled = Event()
        data = b"x" * (1024**2 + 1)
        with patch.object(processes, "_host", Mock()) as host:
            self.invoke(stdin=data, cancelled=cancelled, timeout_seconds=None, output_limit=None, input_limit=None)
            self.assertEqual(data, host.execute.call_args.kwargs["stdin"])
            self.assertIs(cancelled, host.execute.call_args.kwargs["cancelled"])
            self.assertIsNone(host.execute.call_args.kwargs["timeout_seconds"])
            self.assertIsNone(host.execute.call_args.kwargs["output_limit"])
