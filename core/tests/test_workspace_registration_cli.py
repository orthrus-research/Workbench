"""Textual can register a workspace with Core's exact saved revision."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_core.settings_cli import main
from workbench_core.user_preferences import UserPreferencesError, load_workspaces


class WorkspaceRegistrationCliTests(unittest.TestCase):
    def test_json_add_uses_revision_and_does_not_create_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "Supersymmetry"
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                initial = load_workspaces()
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, main([
                        "workspace", "add", "susy-dev", str(workspace),
                        "--default", "--expected-record-id", initial["record_id"],
                        "--json",
                    ]))
                saved = json.loads(output.getvalue())
                self.assertEqual(saved, load_workspaces())
                self.assertEqual("susy-dev", saved["default"])
                self.assertEqual(str(workspace), saved["entries"][0]["path"])
                self.assertFalse(workspace.exists())
                with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
                    main([
                        "workspace", "add", "other", str(home / "Other"),
                        "--expected-record-id", initial["record_id"], "--json",
                    ])
                self.assertEqual(saved, load_workspaces())


if __name__ == "__main__":
    unittest.main()
