"""Focused tests for the credential-free launcher setup binding."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/project-intelligence/src"))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell import launcher_setup, runtime_launch  # noqa: E402


class LauncherSetupTests(unittest.TestCase):
    def _fixture(self, root: Path, *, account: bool = True) -> tuple[Path, Path]:
        executable = root / "launcher-bin/prismlauncher"
        executable.parent.mkdir()
        executable.write_text(
            "#!/bin/sh\nprintf 'PrismLauncher 11.0.3-test\\n'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        data = root / "launcher-data"
        data.mkdir()
        (data / "prismlauncher.cfg").write_text(
            "InstanceDir=instances\n", encoding="utf-8"
        )
        if account:
            # Presence is the only admitted observation. Invalid bytes prove
            # that launcher setup does not parse or retain account contents.
            (data / "accounts.json").write_bytes(b"\xffsecret-fixture\x00")
        return executable, data

    def _main(
        self, arguments: list[str], *, record: Path
    ) -> tuple[int, str, str]:
        output = io.StringIO()
        error = io.StringIO()
        status = launcher_setup.main(
            arguments,
            root=REPOSITORY_ROOT,
            input_stream=io.StringIO(),
            output=output,
            error=error,
            record_path=record,
        )
        return status, output.getvalue(), error.getvalue()

    def test_reviewed_binding_is_applied_and_reused_without_reading_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, data = self._fixture(root)
            account_before = (data / "accounts.json").read_bytes()
            record = root / "config/launcher-v1.json"
            selected = [
                "--family",
                "prism",
                "--executable",
                str(executable),
                "--launcher-root",
                str(data),
            ]

            status, output, error = self._main(
                ["--plan", "--json", *selected], record=record
            )
            plan = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("ready", plan["state"])
            self.assertFalse(record.exists())

            status, output, error = self._main(
                ["--apply", plan["plan_id"], "--json", *selected],
                record=record,
            )
            result = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("configured", result["outcome"])
            self.assertEqual("initialized", result["readiness"]["tooling"])
            self.assertEqual("ready", result["readiness"]["account"])
            self.assertEqual("not-materialized", result["readiness"]["instance"])
            self.assertEqual("not-attempted", result["readiness"]["launch"])
            self.assertEqual(account_before, (data / "accounts.json").read_bytes())

            status, output, error = self._main(
                ["--check", "--json"], record=record
            )
            check = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("ready", check["state"])
            self.assertTrue(check["boundary"]["account"].startswith("presence-only"))
            account = next(
                row for row in check["dependencies"]
                if row["id"] == "launcher-account"
            )
            self.assertIn("did not open", account["detail"])
            self.assertEqual(account_before, (data / "accounts.json").read_bytes())

    def test_missing_account_does_not_block_tooling_but_launch_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, data = self._fixture(root, account=False)
            record = root / "config/launcher-v1.json"

            selected = [
                "--family", "prism", "--executable", str(executable),
                "--launcher-root", str(data),
            ]
            status, output, error = self._main(
                ["--plan", "--json", *selected], record=record
            )
            plan = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("ready", plan["state"])
            self.assertEqual([], plan["blockers"])
            account = next(
                row for row in plan["dependencies"]
                if row["id"] == "launcher-account"
            )
            self.assertFalse(account["required"])
            self.assertEqual("missing-manual", account["state"])
            self.assertFalse(record.exists())

            status, output, error = self._main(
                ["--apply", plan["plan_id"], "--json", *selected], record=record
            )
            result = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("configured", result["outcome"])
            self.assertEqual("initialized", result["readiness"]["tooling"])
            self.assertEqual("missing-manual", result["readiness"]["account"])
            self.assertTrue(record.is_file())

            status, output, error = self._main(
                ["--check", "--json"], record=record
            )
            check = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("ready", check["state"])
            self.assertEqual("initialized", check["tooling"])
            self.assertEqual("missing-manual", check["account"])

            status, output, error = self._main(["--check"], record=record)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertIn("Launcher tooling: initialized", output)
            self.assertIn("Quick Setup and account or offline", output)

            with self.assertRaisesRegex(
                runtime_launch.RuntimeLaunchError, "Quick Setup.*offline profile"
            ):
                runtime_launch._probe_launcher_root(data, "prism")

    def test_managed_prism_path_fills_omitted_launcher_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, data = self._fixture(root)
            record = root / "config/launcher-v1.json"
            with patch.object(
                launcher_setup, "inspect_tools",
                return_value={"tools": {"prism": {"executable": str(executable)}}},
            ):
                status, output, error = self._main(
                    ["--plan", "--json", "--family", "prism",
                     "--launcher-root", str(data)], record=record
                )
            plan = json.loads(output)
            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("ready", plan["state"])
            self.assertEqual(str(executable), plan["selection"]["executable"])

    def test_saved_paths_fill_only_omitted_runtime_launch_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, data = self._fixture(root)
            config = root / "config"
            record = config / "launcher-v1.json"
            launcher_setup._write_launcher_record(
                record,
                {
                    "family": "prism",
                    "executable": str(executable),
                    "root": str(data),
                },
            )
            environment = {"WORKBENCH_CONFIG_HOME": str(config)}

            filled = launcher_setup.launcher_defaults_for_runtime(
                ["runtime-launch", "/pack"], environment=environment
            )
            self.assertEqual("prism", filled[filled.index("--launcher") + 1])
            self.assertEqual(
                str(executable),
                filled[filled.index("--launcher-executable") + 1],
            )
            self.assertEqual(
                str(data), filled[filled.index("--launcher-root") + 1]
            )

            explicit = [
                "runtime-launch",
                "/pack",
                "--launcher",
                "multimc",
                "--launcher-executable",
                "/other/multimc",
                "--launcher-root",
                "/other/root",
            ]
            self.assertEqual(
                explicit,
                launcher_setup.launcher_defaults_for_runtime(
                    explicit, environment=environment
                ),
            )
            explicit_equals = [
                "runtime-launch",
                "/pack",
                "--launcher=multimc",
                "--launcher-executable=/other/multimc",
                "--launcher-root=/other/root",
            ]
            self.assertEqual(
                explicit_equals,
                launcher_setup.launcher_defaults_for_runtime(
                    explicit_equals, environment=environment
                ),
            )
            with self.assertRaisesRegex(
                launcher_setup.LauncherSetupError, "different family"
            ):
                launcher_setup.launcher_defaults_for_runtime(
                    ["runtime-launch", "/pack", "--launcher", "multimc"],
                    environment=environment,
                )
            with self.assertRaisesRegex(
                launcher_setup.LauncherSetupError, "requires a value"
            ):
                launcher_setup.launcher_defaults_for_runtime(
                    ["runtime-launch", "/pack", "--launcher="],
                    environment=environment,
                )

    def test_tampered_binding_fails_identity_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, data = self._fixture(root)
            record = root / "launcher-v1.json"
            launcher_setup._write_launcher_record(
                record,
                {
                    "family": "prism",
                    "executable": str(executable),
                    "root": str(data),
                },
            )
            value = json.loads(record.read_text(encoding="utf-8"))
            value["selection"]["root"] = str(root / "substituted")
            record.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                launcher_setup.LauncherSetupError, "identity"
            ):
                launcher_setup.load_launcher_record(record)


if __name__ == "__main__":
    unittest.main()
