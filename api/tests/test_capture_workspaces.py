"""Attempt-bound capture workspace API dispatch and fail-closed binding."""

from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_api import capture_workspaces as port
from workbench_api.managed_attempts import ManagedAttemptReference


class CaptureWorkspacesPortTests(unittest.TestCase):
    def test_unbound_and_incomplete_hosts_refuse(self) -> None:
        reference = ManagedAttemptReference("fixture", "fixture-" + "0" * 32,
                                            Path("/unused"), Path("/unused/attempt"), "store")
        with patch.object(port, "_host", None):
            with self.assertRaisesRegex(port.CaptureWorkspaceHostError, "no Core capture workspace host"):
                port.capture_execution_workspace(reference)
            with self.assertRaisesRegex(port.CaptureWorkspaceHostError, "incomplete"):
                port.bind_capture_workspaces(object())

    def test_exact_reference_is_forwarded_to_selected_host(self) -> None:
        reference = ManagedAttemptReference("fixture", "fixture-" + "0" * 32,
                                            Path("/unused"), Path("/unused/attempt"), "store")
        workspace = object()

        class Host:
            def execution(self, attempt):
                self.seen = attempt
                return workspace

        host = Host()
        with patch.object(port, "_host", host):
            self.assertIs(workspace, port.capture_execution_workspace(reference))
        self.assertIs(reference, host.seen)


if __name__ == "__main__":
    unittest.main()
