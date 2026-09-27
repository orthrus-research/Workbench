from __future__ import annotations

import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / "api/src", ROOT / "core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_api.long_lived_processes import (  # noqa: E402
    LongLivedProcessError,
    LongLivedProcessRequest,
    ProcessAbsence,
    StagedToolProcessRequest,
    bind_long_lived_process_host,
    observe_process_absence,
    reserve_long_lived_process,
    reserve_staged_tool_process,
)
from workbench_core.long_lived_processes import HOST  # noqa: E402


class LongLivedProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        bind_long_lived_process_host(HOST)

    def _request(self, root: Path, *, host_os: str = "linux") -> LongLivedProcessRequest:
        return LongLivedProcessRequest(
            session_id="workbench-groovy-language-session:fixture",
            host_os=host_os,
            instance_root=root,
            cwd=root,
            instance_device=root.stat().st_dev,
            instance_inode=root.stat().st_ino,
            launch_receipt_sha256="sha256:" + "a" * 64,
            command=("/exact/prism", "--launch", "fixture"),
        )

    @unittest.skipUnless(platform.system() == "Linux", "Linux containment gate")
    def test_linux_restartable_scope_is_unavailable_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LongLivedProcessError, "restartable containment"):
                reserve_long_lived_process(self._request(Path(directory)))

    @unittest.skipUnless(os.name == "posix", "WSL cross-host route is POSIX only")
    def test_wsl_windows_client_requires_windows_side_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LongLivedProcessError, "Windows-side process broker"):
                reserve_long_lived_process(self._request(Path(directory), host_os="windows"))

    def test_invalid_request_is_not_sent_to_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = self._request(Path(directory))
            invalid = LongLivedProcessRequest(
                session_id=request.session_id,
                host_os=request.host_os,
                instance_root=request.instance_root,
                cwd=request.cwd,
                instance_device=request.instance_device,
                instance_inode=request.instance_inode,
                launch_receipt_sha256="sha256:wrong",
                command=request.command,
            )
            with self.assertRaisesRegex(LongLivedProcessError, "invalid"):
                reserve_long_lived_process(invalid)

    def test_absence_observation_is_trivalent_and_validated(self) -> None:
        class Lease:
            def __init__(self, observation):
                self.observation = observation

            def observe_absence(self):
                return self.observation

        for state in ("active", "absent", "unknown"):
            with self.subTest(state=state):
                self.assertEqual(
                    state,
                    observe_process_absence(Lease(ProcessAbsence(state, "exact scope"))).state,
                )
        with self.assertRaisesRegex(LongLivedProcessError, "invalid"):
            observe_process_absence(Lease(ProcessAbsence("absent", "")))

    @unittest.skipUnless(platform.system() == "Linux", "Linux staged tool gate")
    def test_staged_tool_reservation_refuses_before_stage_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "private/server-template"
            request = StagedToolProcessRequest(
                workspace=root,
                owner_id="workbench-shell",
                stage_parent=target.parent,
                stage_policy="core-posix-exact-v1",
                target=target,
                source_variant_id="sha256:" + "a" * 64,
                source_receipt_sha256="b" * 64,
                tool_artifacts=(
                    ("packwiz-refresh", "c" * 64),
                    ("cleanroom-installer", "d" * 64),
                    ("packwiz-installer", "e" * 64),
                ),
            )
            with self.assertRaisesRegex(LongLivedProcessError, "restartable containment"):
                reserve_staged_tool_process(request)
            self.assertFalse(target.parent.exists())

            invalid = StagedToolProcessRequest(
                workspace=request.workspace,
                owner_id=request.owner_id,
                stage_parent=request.stage_parent,
                stage_policy=request.stage_policy,
                target=request.target,
                source_variant_id=request.source_variant_id,
                source_receipt_sha256=request.source_receipt_sha256,
                tool_artifacts=(("packwiz-refresh", "wrong"),),
            )
            with self.assertRaisesRegex(LongLivedProcessError, "request is invalid"):
                reserve_staged_tool_process(invalid)


if __name__ == "__main__":
    unittest.main()
