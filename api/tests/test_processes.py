from pathlib import Path
from threading import Event
import unittest
from unittest.mock import Mock, patch

from workbench_api import processes


class ProcessPortTests(unittest.TestCase):
    def test_logged_process_requires_core_and_preserves_selected_log(self):
        arguments = dict(
            cwd=Path('/owned/work'), log_path=Path('/owned/log/tool.log'),
            environment={'LANG': 'C'}, cancelled=Event(),
            timeout_seconds=1800, output_limit=16 * 1024 * 1024,
        )
        with patch.object(processes, '_host', None), self.assertRaisesRegex(
            processes.ProcessError, 'logged process host',
        ):
            processes.execute_logged_process(['/bin/true'], **arguments)
        with patch.object(processes, '_host', Mock()) as host:
            expected = processes.LoggedProcessResult(0, arguments['log_path'], 'a' * 64, 5)
            host.execute_logged.return_value = expected
            self.assertIs(expected, processes.execute_logged_process(['/bin/true'], **arguments))
            self.assertEqual(arguments['log_path'], host.execute_logged.call_args.kwargs['log_path'])
            for change in ({'timeout_seconds': 3601}, {'output_limit': 16 * 1024 * 1024 + 1},
                           {'log_path': Path('relative.log')}):
                with self.assertRaises(processes.ProcessError):
                    processes.execute_logged_process(['/bin/true'], **{**arguments, **change})
            host.execute_logged.assert_called_once()

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

    def test_merged_capture_requires_support_and_preserves_raw_result(self):
        arguments = dict(cwd=Path.cwd(), stdin=b'', environment={'LANG': 'C'},
                         cancelled=Event(), timeout_seconds=15, output_limit=4 * 1024 * 1024)
        with patch.object(processes, '_host', None), self.assertRaises(processes.ProcessError):
            processes.execute_merged_process(['/bin/true'], **arguments)
        host = type('ByteOnlyHost', (), {'execute': lambda *args, **kwargs: None})()
        with patch.object(processes, '_host', host):
            with self.assertRaisesRegex(processes.ProcessError, 'merged capture'):
                processes.execute_merged_process(['/bin/true'], **arguments)
        expected = processes.MergedProcessResult(3, b'out\xfferr')
        with patch.object(processes, '_host', Mock()) as merged:
            merged.execute_merged.return_value = expected
            self.assertIs(expected, processes.execute_merged_process(['/bin/true'], **arguments))
            self.assertEqual(15, merged.execute_merged.call_args.kwargs['timeout_seconds'])
            self.assertEqual(4 * 1024 * 1024, merged.execute_merged.call_args.kwargs['output_limit'])


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
