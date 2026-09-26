"""Reviewed managed-tool acquisition remains separate from selection import."""

from __future__ import annotations

import json
import os
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from workbench_core import tooling_provision
from workbench_core.environment_reconstruction import (
    ReconstructionError, apply_tool_import, build_share, plan_import,
    plan_tool_import,
)
from workbench_core.user_preferences import register_workspace, set_workspace_selection

from test_environment_reconstruction import SOURCE_SUITE, _environment, _suite


class EnvironmentToolImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source_suite = _suite(self.root / "source-suite")
        self.target_suite = _suite(self.root / "target-suite")
        self.source_user = self.root / "source-user"
        self.target_user = self.root / "target-user"
        self.source_workspace = self.source_user / "workspace"
        self.target_workspace = self.target_user / "workspace"
        self.source_workspace.mkdir(parents=True)
        self.target_workspace.mkdir(parents=True)
        self.source_environment = _environment(self.source_user)
        self.target_environment = _environment(self.target_user)
        register_workspace("pack", str(self.source_workspace), environment=self.source_environment)
        set_workspace_selection(
            "pack", profile_config=str(self.source_suite / "workbench.toml"),
            environment=self.source_environment,
        )
        for suite in (self.source_suite, self.target_suite):
            profile = suite / "profiles/packs/supersymmetry/profile.yaml"
            old = profile.read_text(encoding="utf-8")
            new = old.replace(
                "    maturity: experimental\n",
                "    source_lock: source-locks/legacy-forge/source-lock.json\n"
                "    maturity: experimental\n", 1,
            )
            self.assertNotEqual(old, new)
            profile.write_text(new, encoding="utf-8")
            source = SOURCE_SUITE / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
            destination = suite / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
            destination.parent.mkdir(parents=True)
            copy2(source, destination)
        self.share = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        host = self.share["lock"]["host_variant"]
        self.key = f"{host['os']}-{host['architecture']}"

    def _plan(self, **options: object) -> dict:
        return plan_tool_import(
            self.target_suite, self.share, workspace=self.target_workspace,
            environment=self.target_environment, **options,
        )

    def _apply(self, plan: dict, **options: object) -> dict:
        return apply_tool_import(
            self.target_suite, self.share, expected_plan_id=plan["plan_id"],
            workspace=self.target_workspace, environment=self.target_environment,
            **options,
        )

    def _attempts(self) -> list[Path]:
        return list((self.target_user / "state/evidence/outputs/workbench-core").glob(
            "*-environment-tool-import-attempt.json"
        ))

    def _results(self) -> list[Path]:
        return list((self.target_user / "state/evidence/outputs/workbench-core").glob(
            "*-environment-tool-import.json"
        ))

    def test_reviewed_acquire_then_reuse_preserves_other_missing_inputs(self) -> None:
        initial = plan_import(
            self.target_suite, self.share, workspace_name="shared",
            workspace=self.target_workspace, environment=self.target_environment,
        )
        self.assertIn("profile-fixture-and-tool-bytes", initial["unresolved_inputs"])
        plan = self._plan()
        self.assertEqual(("ready", "acquire"), (plan["state"], plan["action"]))
        self.assertFalse((self.target_user / "state").exists())
        ready = False
        calls: list[tuple[Path, str, object, object]] = []

        def inspect(state_root: Path, *, key: str) -> dict:
            self.assertEqual(self.target_user / "state", state_root)
            self.assertEqual(self.key, key)
            state = "ready" if ready else "missing"
            return {
                "format": tooling_provision.FORMAT, "host": key,
                "state": "initialized" if ready else "attention",
                "tools": {name: {"state": state, "executable": str(state_root / name) if ready else None}
                          for name in ("prism", "packwiz")},
            }

        def prepare(state_root: Path, *, key: str, seed_dir: object, go_executable: object) -> dict:
            nonlocal ready
            self.assertEqual(1, len(self._attempts()))
            self.assertEqual([], self._results())
            calls.append((state_root, key, seed_dir, go_executable))
            ready = True
            return inspect(state_root, key=key)

        with patch.object(tooling_provision, "inspect_tools", side_effect=inspect), patch.object(
            tooling_provision, "prepare_tools", side_effect=prepare,
        ):
            # The inspected tool state is part of the review ID.
            mocked_plan = self._plan()
            result = self._apply(mocked_plan)
            self.assertEqual("acquired", result["outcome"])
            self.assertEqual([(self.target_user / "state", self.key, None, None)], calls)
            self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
            self.assertEqual(self.share["lock"]["managed_tool_lock"], result["managed_tool_lock"])
            self.assertEqual(1, len(self._results()))
            self.assertEqual(json.loads(self._results()[0].read_text()), {
                key: value for key, value in result.items() if key != "resource"
            })
            self.assertEqual("reuse", self._plan()["action"])
            with patch.object(tooling_provision, "prepare_tools", side_effect=AssertionError("rebuild")):
                reused = self._apply(self._plan())
            self.assertEqual("reused", reused["outcome"])

    def test_failed_provision_retains_prepared_attempt_without_result(self) -> None:
        plan = self._plan()
        with patch.object(tooling_provision, "prepare_tools", side_effect=tooling_provision.ToolingProvisionError("unavailable")):
            with self.assertRaisesRegex(ReconstructionError, "did not complete"):
                self._apply(plan)
        attempts = self._attempts()
        self.assertEqual(1, len(attempts))
        self.assertEqual("prepared", json.loads(attempts[0].read_text())["state"])
        self.assertEqual([], self._results())

    def test_replan_refuses_changed_tool_state_before_prepared_attempt(self) -> None:
        plan = self._plan()
        destination = self.target_user / "state/.workbench/managed-tools" / self.key / "prism"
        destination.mkdir(parents=True)
        with self.assertRaisesRegex(ReconstructionError, "plan changed"):
            self._apply(plan)
        self.assertEqual([], self._attempts())
        self.assertEqual("blocked", self._plan()["state"])

    def test_policy_and_host_drift_block_without_acquisition(self) -> None:
        policy = tooling_provision.ASSETS[self.key]["prism"]
        with patch.dict(policy, {"size": policy["size"] + 1}):
            drifted = self._plan()
            self.assertEqual("blocked", drifted["state"])
            self.assertIn("policy differs", "; ".join(drifted["blockers"]))
        other_host = "windows" if self.key.startswith("linux-") else "linux"
        with patch("workbench_core.environment_tool_import.host_platform", return_value={"os": other_host, "architecture": "x64"}):
            other = self._plan()
            self.assertEqual("blocked", other["state"])
            self.assertIn("another executing host", "; ".join(other["blockers"]))
        self.assertEqual([], self._attempts())

    @unittest.skipIf(os.name == "nt", "POSIX mode proxy for WSL mounts")
    def test_nonprivate_state_root_blocks_before_acquisition(self) -> None:
        state = self.target_user / "state"
        state.mkdir()
        state.chmod(0o755)
        unsafe = self._plan()
        self.assertEqual("blocked", unsafe["state"])
        self.assertIn("not owner-private", "; ".join(unsafe["blockers"]))
        self.assertEqual([], self._attempts())

    def test_older_share_has_no_implicit_tool_acquisition(self) -> None:
        older = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True,
        )
        with self.assertRaisesRegex(ReconstructionError, "requires a V3 share"):
            plan_tool_import(
                self.target_suite, older, workspace=self.target_workspace,
                environment=self.target_environment,
            )
        self.assertEqual([], self._attempts())


if __name__ == "__main__":
    unittest.main()
