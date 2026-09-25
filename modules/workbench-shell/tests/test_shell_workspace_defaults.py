"""Shell workspace commands consume Core's resolved workspace."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
for source in (
    REPOSITORY_ROOT / "api/src",
    REPOSITORY_ROOT / "core/src",
    REPOSITORY_ROOT / "modules/project-intelligence/src",
    REPOSITORY_ROOT / "modules/workbench-shell/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_api import ExecutionContext  # noqa: E402
from workbench_shell import commands  # noqa: E402


class ShellWorkspaceDefaultsTests(unittest.TestCase):
    def test_open_uses_core_default_and_explicit_argument_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "selected"
            explicit = root / "explicit"
            context = ExecutionContext(selected, root / "state")
            with patch.dict(os.environ, {"WORKBENCH_WORKSPACE": str(root / "stale")}):
                with patch("workbench_shell.product_spine_cli.home_main", return_value=0) as home:
                    self.assertEqual(0, commands.open(["--json"], context=context))
                    self.assertEqual([str(selected), "--json"], home.call_args.args[0])
                    self.assertEqual(0, commands.open([str(explicit), "--json"], context=context))
                    self.assertEqual([str(explicit), "--json"], home.call_args.args[0])

    def test_doctor_uses_core_default_and_explicit_argument_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "selected"
            explicit = root / "explicit"
            context = ExecutionContext(selected, root / "state")
            report = {"summary": {"status": "ready"}}
            with patch.dict(os.environ, {"WORKBENCH_WORKSPACE": str(root / "stale")}):
                with patch(
                    "workbench_project_intelligence.workspace_doctor.new_report",
                    return_value=report,
                ) as inspect:
                    with redirect_stdout(StringIO()):
                        self.assertEqual(0, commands.doctor(["--json"], context=context))
                    inspect.assert_called_once_with(selected, requested_path=selected)
                    inspect.reset_mock()
                    with redirect_stdout(StringIO()):
                        self.assertEqual(
                            0, commands.doctor([str(explicit), "--json"], context=context)
                        )
                    inspect.assert_called_once_with(explicit, requested_path=explicit)


if __name__ == "__main__":
    unittest.main()
