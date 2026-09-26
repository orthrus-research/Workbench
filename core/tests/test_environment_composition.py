"""A V3 share can link independently acquired Core inputs without overstating closure."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_core.environment_reconstruction import (
    ReconstructionError, apply_environment_composition, apply_import,
    apply_tool_import, build_share, plan_environment_composition, plan_import,
    plan_tool_import,
)
from workbench_core import tooling_provision
from workbench_core.user_preferences import set_workspace_selection

from . import test_environment_project_import as project_fixture


class EnvironmentCompositionTests(unittest.TestCase):
    # Reuse the local-Git, two-suite fixture without inheriting its test cases.
    setUp = project_fixture.EnvironmentProjectImportTests.setUp
    _git = project_fixture.EnvironmentProjectImportTests._git
    _attach_source_lock = project_fixture.EnvironmentProjectImportTests._attach_source_lock
    _plan = project_fixture.EnvironmentProjectImportTests._plan
    _apply = project_fixture.EnvironmentProjectImportTests._apply

    def _acquired(self) -> tuple[dict, dict, dict]:
        self.share = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        reviewed = plan_import(
            self.target_suite, self.share, workspace_name="shared",
            workspace=self.target_workspace, environment=self.target_environment,
        )
        selection = apply_import(
            self.target_suite, self.share, expected_plan_id=reviewed["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        project = self._apply(self._plan())
        tool_plan = plan_tool_import(
            self.target_suite, self.share, workspace=self.target_workspace,
            environment=self.target_environment,
        )
        tools = apply_tool_import(
            self.target_suite, self.share, expected_plan_id=tool_plan["plan_id"],
            workspace=self.target_workspace, environment=self.target_environment,
        )
        return selection, project, tools

    def _arguments(self, selected: dict, project: dict, tools: dict) -> dict:
        return {
            "workspace_name": "shared", "workspace": self.target_workspace,
            "selection_resource_id": selected["resource"]["resource_id"],
            "project_resource_id": project["resource"]["resource_id"],
            "tool_resource_id": tools["resource"]["resource_id"],
            "environment": self.target_environment,
        }

    def _ready_tools(self) -> None:
        def inspect(state_root: Path, *, key: str) -> dict:
            state = "ready" if self.tool_ready else "missing"
            return {
                "format": tooling_provision.FORMAT,
                "host": key,
                "state": "initialized" if self.tool_ready else "attention",
                "tools": {
                    name: {
                        "state": state,
                        "executable": str(state_root / name) if self.tool_ready else None,
                    }
                    for name in ("prism", "packwiz")
                },
            }

        self.tool_ready = True
        for target, name, replacement in (
            (tooling_provision, "inspect_tools", inspect),
            ("workbench_core.environment_tool_import", "inspect_locked_managed_tools",
             lambda lock, *, state_root: {"state": "ready" if self.tool_ready else "bytes-unavailable"}),
            ("workbench_core.environment_composition", "inspect_locked_managed_tools",
             lambda lock, *, state_root: {"state": "ready" if self.tool_ready else "bytes-unavailable"}),
        ):
            started = patch.object(target, name, replacement) if not isinstance(target, str) else patch(f"{target}.{name}", replacement)
            started.start()
            self.addCleanup(started.stop)

    def test_reviewed_composition_links_three_core_results_and_keeps_missing_inputs(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        plan = plan_environment_composition(self.target_suite, self.share, **arguments)
        self.assertEqual("ready", plan["state"])
        self.assertEqual({"selection", "project", "managed_tools"}, set(plan["resources"]))
        self.assertNotIn("workspace-project-bytes", plan["unresolved_inputs"])
        self.assertIn("optional-module-packages", plan["unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", plan["unresolved_inputs"])
        self.assertFalse(list((self.target_user / "state/evidence/outputs/workbench-core").glob(
            "*-environment-composition.json")))
        result = apply_environment_composition(
            self.target_suite, self.share, expected_plan_id=plan["plan_id"], **arguments,
        )
        self.assertEqual(plan["resources"], result["resources"])
        self.assertTrue(Path(result["resource"]["path"]).is_file())

    def test_changed_project_and_tools_refuse_a_reviewed_composition(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        plan = plan_environment_composition(self.target_suite, self.share, **arguments)
        checkout = Path(project["managed_destination"])
        marker = checkout / "pack.marker"
        marker.write_bytes(b"changed project\n")
        with self.assertRaisesRegex((ReconstructionError, OSError), "worktree differs"):
            apply_environment_composition(
                self.target_suite, self.share, expected_plan_id=plan["plan_id"], **arguments,
            )
        marker.write_bytes(b"exact project bytes\n")
        self.tool_ready = False
        with self.assertRaisesRegex(ReconstructionError, "managed-tool bytes changed"):
            apply_environment_composition(
                self.target_suite, self.share, expected_plan_id=plan["plan_id"], **arguments,
            )
        self.assertFalse(list((self.target_user / "state/evidence/outputs/workbench-core").glob(
            "*-environment-composition.json")))

    def test_swapped_or_stale_result_identity_is_refused(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        plan = plan_environment_composition(self.target_suite, self.share, **arguments)
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_environment_composition(
                self.target_suite, self.share, expected_plan_id="stale", **arguments,
            )
        arguments["tool_resource_id"] = project["resource"]["resource_id"]
        with self.assertRaisesRegex(ReconstructionError, "distinct Core result"):
            plan_environment_composition(self.target_suite, self.share, **arguments)
        self.assertEqual("ready", plan["state"])

    def test_changed_local_selection_cannot_be_composed(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        set_workspace_selection(
            "shared", profile_config=str(self.target_suite / "another-selection.toml"),
            environment=self.target_environment,
        )
        with self.assertRaisesRegex(ReconstructionError, "local workspace selection changed"):
            plan_environment_composition(self.target_suite, self.share, **arguments)


if __name__ == "__main__":
    unittest.main()
