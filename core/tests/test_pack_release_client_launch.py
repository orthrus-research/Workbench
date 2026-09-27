"""Core Prism launch custody, installed-instance binding and quiet lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from workbench_core import pack_release_client_launch as launch
from workbench_core.durable_records import publish_create_once_bytes
from workbench_core.pack_release_local import _canonical
from workbench_core.runner import RunnerError


ROOT = Path(__file__).resolve().parents[2]


class ReleaseClientLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.launcher = self.root / "Prism"
        self.instance_id = "workbench-supersymmetry-import-123456789abc"
        self.instance = self.launcher / "instances" / self.instance_id
        self.executable = self.root / "managed/PrismLauncher"
        for path in (self.state, self.launcher, self.launcher / "instances",
                     self.instance, self.executable.parent):
            path.mkdir(mode=0o700)
        (self.launcher / "prismlauncher.cfg").write_text("[General]\n", encoding="utf-8")
        (self.instance / "instance.cfg").write_text("[General]\nname=Imported\n", encoding="utf-8")
        (self.instance / "mmc-pack.json").write_text("{}\n", encoding="utf-8")
        self._script("#!/bin/sh\nexit 0\n")
        self.install_id = "workbench-pack-release-client-install-plan:sha256:" + "a" * 64
        self.receipt = {
            "format": "workbench-pack-release-client-install-receipt-v1",
            "schema_version": 1, "plan_id": self.install_id,
            "instance_path": str(self.instance), "installation_state": "installed",
            "runtime_qualification_state": "not-qualified",
            "source_kind": "user-prism-zip",
        }
        raw = _canonical(self.receipt) + b"\n"
        receipt_path = (self.state / "pack-release-client-installs"
                        / self.install_id.rsplit(":", 1)[-1] / "receipt.json")
        receipt_path.parent.mkdir(mode=0o700, parents=True)
        publish_create_once_bytes(receipt_path, raw, byte_limit=64 * 1024)
        publish_create_once_bytes(self.instance / ".workbench-release-install.json",
                                  raw, byte_limit=64 * 1024)
        self.tool = {"format": "workbench-managed-developer-tools-v1",
                     "host": "linux-x64", "tools": {
                         "prism": {"state": "ready", "executable": str(self.executable)}}}
        inspector = patch.object(launch, "inspect_tools", return_value=self.tool)
        inspector.start()
        self.addCleanup(inspector.stop)

    def _script(self, text: str) -> None:
        self.executable.write_text(text, encoding="utf-8")
        self.executable.chmod(0o700)

    def _plan(self, mode: str = "show") -> dict:
        return launch.plan_release_client_launch(
            state_root=self.state, launcher_root=self.launcher,
            expected_install_plan_id=self.install_id, mode=mode,
        )

    def _run(self, mode: str = "show", **extra: object) -> dict:
        plan = self._plan(mode)
        return launch.run_release_client_launch(
            state_root=self.state, launcher_root=self.launcher,
            expected_install_plan_id=self.install_id,
            expected_launch_plan_id=plan["plan_id"], mode=mode, **extra,
        )

    def test_show_uses_exact_instance_and_writes_quiet_lifecycle_receipt(self) -> None:
        argv_path = self.root / "argv.json"
        self._script("#!/usr/bin/env python3\nimport json,sys\n"
                     f"open({str(argv_path)!r},'w').write(json.dumps(sys.argv[1:]))\n")
        result = self._run()
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(result["launcher_exit_code"], 0)
        self.assertEqual(json.loads(argv_path.read_text(encoding="utf-8")),
                         ["--dir", str(self.launcher), "--show", self.instance_id])
        receipt_path = Path(result["receipt_path"])
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["launch_id"],
                         result["launch_id"])
        self.assertTrue((receipt_path.parent / "requested.json").is_file())
        self.assertNotIn("accounts.json", receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(result["runtime_qualification_state"], "not-qualified")
        self.assertEqual(result["game_lifecycle_state"], "unobserved")

    def test_direct_launch_uses_prism_default_account_and_reports_launcher_failure(self) -> None:
        self._script("#!/bin/sh\nexit 3\n")
        result = self._run("launch")
        self.assertEqual(result["mode"], "launch")
        self.assertEqual(result["outcome"], "process-failed")
        self.assertEqual(result["launcher_exit_code"], 3)

    def test_changed_marker_and_unpinned_launcher_are_rejected(self) -> None:
        marker = self.instance / ".workbench-release-install.json"
        marker.write_bytes(b"different\n")
        with self.assertRaisesRegex(ValueError, "differs from its Core install receipt"):
            self._plan()
        marker.write_bytes(_canonical(self.receipt) + b"\n")
        self.tool["tools"]["prism"] = {"state": "invalid", "executable": None}
        with self.assertRaisesRegex(ValueError, "managed Prism launcher is unavailable"):
            self._plan()

    def test_unreviewed_launch_does_not_write_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "changed after review"):
            launch.run_release_client_launch(
                state_root=self.state, launcher_root=self.launcher,
                expected_install_plan_id=self.install_id,
                expected_launch_plan_id=launch._PLAN_PREFIX + "0" * 64,
            )
        self.assertFalse((self.state / "pack-release-client-launches").exists())

    def test_supervisor_failure_retains_request_and_failure_outcome(self) -> None:
        with patch.object(launch, "supervise_process", side_effect=RunnerError("synthetic")):
            with self.assertRaisesRegex(RunnerError, "synthetic"):
                self._run()
        launches = self.state / "pack-release-client-launches"
        receipts = list(launches.glob("*/completed.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(json.loads(receipts[0].read_text(encoding="utf-8"))["outcome"],
                         "supervisor-failed")
        self.assertTrue((receipts[0].parent / "requested.json").is_file())

    def test_timeout_interrupts_owned_prism_invocation(self) -> None:
        self._script("#!/usr/bin/env python3\nimport time\n"
                     "while True:\n    time.sleep(0.1)\n")
        result = self._run(timeout_seconds=0.2)
        self.assertEqual(result["outcome"], "cancelled")
        self.assertIsNotNone(result["cancellation"])

    @unittest.skipUnless(sys.platform == "linux", "Linux Prism process groups")
    def test_sigterm_cancels_prism_and_completes_receipt(self) -> None:
        prism_pid = self.root / "prism.pid"
        completed = self.root / "launch-result.json"
        self._script(
            "#!/usr/bin/env python3\nimport os,time\nfrom pathlib import Path\n"
            f"Path({str(prism_pid)!r}).write_text(str(os.getpid()))\n"
            "while True:\n    time.sleep(0.1)\n"
        )
        launch_plan_id = self._plan()["plan_id"]
        invoke = self.root / "invoke-launch.py"
        invoke.write_text(
            "import json\nfrom pathlib import Path\n"
            "from workbench_core import pack_release_client_launch as launch\n"
            f"launch.inspect_tools = lambda *args, **kwargs: {self.tool!r}\n"
            "result = launch.run_release_client_launch(\n"
            f"    state_root=Path({str(self.state)!r}),\n"
            f"    launcher_root=Path({str(self.launcher)!r}),\n"
            f"    expected_install_plan_id={self.install_id!r},\n"
            f"    expected_launch_plan_id={launch_plan_id!r},\n"
            ")\n"
            f"Path({str(completed)!r}).write_text(json.dumps(result))\n",
            encoding="utf-8",
        )
        environment = dict(os.environ)
        source_paths = [str(Path(launch.__file__).resolve().parents[1]), str(ROOT / "api/src")]
        if environment.get("PYTHONPATH"):
            source_paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(source_paths)
        process = subprocess.Popen(
            [sys.executable, str(invoke)], cwd=self.root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while not prism_pid.is_file() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(prism_pid.is_file(), "managed Prism did not start")
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, (stdout + stderr).decode(errors="replace"))
            result = json.loads(completed.read_text(encoding="utf-8"))
            self.assertEqual(result["outcome"], "cancelled")
            self.assertEqual(result["cancellation"], "interrupt-requested")
            receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["outcome"], "cancelled")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(prism_pid.read_text(encoding="utf-8")), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
