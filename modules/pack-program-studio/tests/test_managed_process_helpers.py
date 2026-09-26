"""Core custody of bounded Windows/WSL helper processes for managed sessions."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "api/src",
    ROOT / "core/src",
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/pack-program-studio/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_api.processes import ProcessError, ProcessResult  # noqa: E402
from workbench_core.host_services import install_local_host_services  # noqa: E402
from workbench_pack_program_studio.managed_session import (  # noqa: E402
    _powershell, _server_workspace_uri,
)
from workbench_pack_program_studio.model import PackProgramError  # noqa: E402


class ManagedProcessHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()

    def _executable(self, root: Path, name: str, source: str) -> Path:
        path = root / name
        path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
        path.chmod(0o700)
        return path

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_windows_custody_helper_runs_through_core_with_exact_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = self._executable(
                Path(directory), "powershell.exe",
                "import os, sys\n"
                "sys.stdout.write(os.environ['WORKBENCH_GROOVY_INSTANCE_TOKEN'] + ':' + "
                "os.environ['WORKBENCH_GROOVY_PROCESS_IDS'] + ':' + "
                "os.environ['WORKBENCH_GROOVY_ENDPOINT_PORT'] + '\\n')\n",
            )
            with patch(
                "workbench_pack_program_studio.managed_session._powershell_executable",
                return_value=str(executable),
            ):
                result = _powershell("unused helper script", instance_id="workbench-fixture", pids=(42,), port=31337)
        self.assertEqual("workbench-fixture:42:31337", result)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_windows_custody_helper_failure_stays_a_session_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = self._executable(
                Path(directory), "powershell.exe",
                "import sys\nsys.stderr.write('ownership refused\\n')\nsys.exit(7)\n",
            )
            with patch(
                "workbench_pack_program_studio.managed_session._powershell_executable",
                return_value=str(executable),
            ):
                with self.assertRaisesRegex(PackProgramError, "ownership refused"):
                    _powershell("unused helper script", instance_id="workbench-fixture")

    def test_core_process_failure_stays_a_session_failure(self) -> None:
        with patch(
            "workbench_pack_program_studio.managed_session._powershell_executable",
            return_value=str(Path(sys.executable).resolve()),
        ), patch(
            "workbench_pack_program_studio.managed_session.execute_process",
            side_effect=ProcessError("timed out"),
        ):
            with self.assertRaisesRegex(PackProgramError, "Windows process custody failed: timed out"):
                _powershell("unused helper script", instance_id="workbench-fixture")

    def test_windows_helper_passes_exact_core_process_policy(self) -> None:
        executable = str(Path(sys.executable).resolve())
        with patch(
            "workbench_pack_program_studio.managed_session._powershell_executable",
            return_value=executable,
        ), patch(
            "workbench_pack_program_studio.managed_session.execute_process",
            return_value=ProcessResult(0, b"[]\n", b""),
        ) as execute:
            self.assertEqual("[]", _powershell(
                "helper", instance_id="workbench-fixture", pids=(42,), port=31337,
            ))
        self.assertEqual(executable, execute.call_args.args[0][0])
        self.assertEqual(15, execute.call_args.kwargs["timeout_seconds"])
        self.assertEqual(1024 * 1024, execute.call_args.kwargs["output_limit"])
        self.assertEqual(b"", execute.call_args.kwargs["stdin"])
        environment = execute.call_args.kwargs["environment"]
        self.assertEqual("workbench-fixture", environment["WORKBENCH_GROOVY_INSTANCE_TOKEN"])
        self.assertEqual("42", environment["WORKBENCH_GROOVY_PROCESS_IDS"])
        self.assertEqual("31337", environment["WORKBENCH_GROOVY_ENDPOINT_PORT"])

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_wsl_workspace_translation_uses_core_process_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = self._executable(
                root, "wslpath",
                r'''import sys
sys.stdout.write("C:\\Pack Root\\groovy\n")
''',
            )
            with patch(
                "workbench_pack_program_studio.managed_session.shutil.which",
                return_value=str(executable),
            ):
                self.assertEqual(
                    "file:///C:/Pack%20Root/groovy",
                    _server_workspace_uri(root, bridge_required=True),
                )


if __name__ == "__main__":
    unittest.main()
