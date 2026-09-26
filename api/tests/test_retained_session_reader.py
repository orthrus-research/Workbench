"""Exact console-session lookup requires a Core-bound reader."""

from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_api import sessions


class _Reader:
    def resolve(self, workspace: Path, session_id: str) -> Path:
        return workspace / session_id


class RetainedSessionReaderTests(unittest.TestCase):
    def test_binding_forwards_exact_workspace_and_id(self) -> None:
        with patch.object(sessions, "_reader", None):
            with self.assertRaisesRegex(sessions.SessionError, "no retained session reader"):
                sessions.resolve_retained_session(Path("/selected"), "session-001")
            reader = _Reader()
            sessions.bind_retained_session_reader(reader)
            self.assertEqual(
                Path("/selected/session-001"),
                sessions.resolve_retained_session(Path("/selected"), "session-001"),
            )
            sessions.bind_retained_session_reader(reader)
            with self.assertRaisesRegex(sessions.SessionError, "different"):
                sessions.bind_retained_session_reader(_Reader())


if __name__ == "__main__":
    unittest.main()
