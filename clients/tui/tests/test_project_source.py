"""A developer can acquire a GitHub branch from Textual without using a shell."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static

from test_setup_screen import fake_core
from workbench_tui.app import (
    ProjectBranchPicker, ProjectSourceReadyScreen, ProjectSourceScreen,
    ReviewModal, WorkbenchApp, WorkflowsScreen,
    _source_suggestion,
)
from workbench_tui.core_client import CoreClient, CoreClientError


COMMIT = "a" * 40
PLAN_ID = "workbench-project-acquisition-plan-v3:sha256:" + "b" * 64


def source_core():
    core = fake_core("/home/test/Supersymmetry")
    core.modules = AsyncMock(return_value=[
        {"id": "project-intelligence", "state": "available"},
        {"id": "pack-program-studio", "state": "available"},
        {"id": "axiom", "state": "available"},
    ])
    core.profiles = AsyncMock(return_value=[
        {"id": "supersymmetry", "state": "available"},
        {"id": "cleanroom", "state": "available"},
    ])
    core.workspace_choices = AsyncMock(return_value={
        "record_id": "workspaces-before", "default": "current",
        "entries": [{"name": "current", "path": "/home/test/Supersymmetry"}],
    })
    core.project_branches = AsyncMock(return_value={
        "format": "workbench-project-branch-list-v1",
        "repository": "https://github.com/SymmetricDevs/Supersymmetry.git",
        "branches": [{"name": "master-ceu", "commit": COMMIT},
                     {"name": "feature/circuits", "commit": "c" * 40}],
        "truncated": False,
    })
    core.project_acquire_plan = AsyncMock()
    core.project_acquire_apply = AsyncMock()
    core.register_workspace = AsyncMock(return_value={"record_id": "workspaces-after"})
    return core


class ProjectSourceClientTests(unittest.IsolatedAsyncioTestCase):
    def test_wsl_drive_workspace_suggests_linux_home_checkout(self) -> None:
        destination, name = _source_suggestion(
            "feature/new-work", "/mnt/c/Users/test/Supersymmetry",
        )
        self.assertEqual(str(Path.home() / "Workbench" / "Supersymmetry-feature-new-work"),
                         destination)
        self.assertEqual("susy-feature-new-work", name)

    async def test_core_client_uses_exact_branch_plan_and_apply(self) -> None:
        client = CoreClient(("workbench",))
        destination = "/home/user/Supersymmetry-circuits"
        plan = {
            "format": "workbench-project-acquisition-plan-v3", "plan_id": PLAN_ID,
            "source_kind": "branch", "branch_name": "feature/circuits",
            "destination": destination, "source_only": True,
            "remote_url": "https://github.com/example/Supersymmetry.git",
            "resolved_commit": COMMIT,
        }
        result = {
            "format": "workbench-project-acquisition-result-v3", "plan_id": PLAN_ID,
            "source_kind": "branch", "branch_name": "feature/circuits",
            "destination": destination, "source_only": True,
            "resolved_commit": COMMIT, "receipt_path": "/state/receipt.json",
            "remote_url": "https://github.com/example/Supersymmetry.git",
            "profile_compatible": True, "profile_diagnostic": None,
        }
        client.json_record = AsyncMock(side_effect=[plan, result])
        reviewed = await client.project_acquire_plan(
            "feature/circuits", destination,
            repository="https://github.com/example/Supersymmetry.git",
            source_only=True,
        )
        self.assertEqual(plan, reviewed)
        self.assertEqual(result, await client.project_acquire_apply(
            reviewed, "feature/circuits", destination,
            repository="https://github.com/example/Supersymmetry.git",
            source_only=True,
        ))
        client.json_record.assert_any_await(
            "project", "acquire", "supersymmetry", "--branch", "feature/circuits",
            "--destination", destination, "--repository",
            "https://github.com/example/Supersymmetry.git", "--source-only",
            "--plan", "--json", timeout=150,
        )
        client.json_record.assert_awaited_with(
            "project", "acquire", "supersymmetry", "--branch", "feature/circuits",
            "--destination", destination, "--repository",
            "https://github.com/example/Supersymmetry.git", "--source-only",
            "--apply", PLAN_ID, "--json", timeout=2400,
        )

    async def test_branch_list_is_read_only_and_validated(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value={
            "format": "workbench-project-branch-list-v1", "repository": "https://github.com/SymmetricDevs/Supersymmetry.git",
            "branches": [{"name": "master-ceu", "commit": COMMIT}], "truncated": False,
        })
        await client.project_branches()
        client.json_record.assert_awaited_with(
            "project", "acquire", "supersymmetry", "--list-branches", "--json",
            timeout=150,
        )


class ProjectSourceInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(80):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected branch acquisition state")

    async def test_home_and_workflows_open_keyboard_first_branch_flow(self) -> None:
        core = source_core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(64, 22)) as pilot:
            await self._settle(pilot, lambda: app.view.modules_loaded and app.view.profiles_loaded
                               and app.view.catalog is not None)
            home = app.query_one("#home-actions", OptionList)
            await self._settle(pilot, lambda: app.focused is home)
            self.assertFalse(home.get_option("github-source").disabled)
            home.highlighted = next(i for i, option in enumerate(home.options)
                                    if option.id == "github-source")
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, ProjectSourceScreen))
            source = app.screen
            self.assertTrue(source.query_one("#project-source-mode", Select).has_class(
                "keyboard-selected"))
            self.assertIsNone(app.focused)
            self.assertTrue(source.query_one("#project-source-preview", Button).display)
            self.assertTrue(source.query_one("#project-source-back", Button).display)
            await pilot.press("escape")
            await self._settle(pilot, lambda: not isinstance(app.screen, ProjectSourceScreen))
            app.open_workflows()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkflowsScreen)
                               and bool(app.screen.query("#workflow-brief")))
            workflow = app.screen
            self.assertIn("project.acquire-journey",
                          {row["command_id"] for row in workflow._actions()})
            workflow._select("project.acquire-journey")
            workflow.query_one("#workflow-run", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ProjectSourceScreen))

    async def test_branch_picker_and_manual_fallback(self) -> None:
        core = source_core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(64, 22)) as pilot:
            await self._settle(pilot, lambda: app.view.modules_loaded and app.view.profiles_loaded)
            app.open_project_source()
            await self._settle(pilot, lambda: isinstance(app.screen, ProjectSourceScreen))
            source = app.screen
            source.query_one("#project-source-browse-branches", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ProjectBranchPicker))
            picker = app.screen
            await pilot.press("/", "c", "i", "r", "c", "u", "i", "t", "s", "enter")
            await self._settle(pilot, lambda: app.focused is picker.query_one(
                "#project-branch-list", OptionList))
            await pilot.press("enter")
            await self._settle(pilot, lambda: app.screen is source)
            self.assertEqual("feature/circuits", source.query_one(
                "#project-source-branch", Input).value)
            core.project_branches = AsyncMock(side_effect=CoreClientError("GitHub unavailable"))
            source.query_one("#project-source-browse-branches", Button).press()
            await self._settle(pilot, lambda: "Could not list branches" in str(
                source.query_one("#project-source-status", Static).content))
            source.query_one("#project-source-branch", Input).value = "manual/new-work"
            self.assertEqual("manual/new-work", source.query_one(
                "#project-source-branch", Input).value)

    async def test_exact_review_then_acquire_and_register_workspace(self) -> None:
        core = source_core()
        with tempfile.TemporaryDirectory() as folder:
            destination = str(Path(folder) / "Supersymmetry-circuits")
            plan = {
                "format": "workbench-project-acquisition-plan-v3", "plan_id": PLAN_ID,
                "source_kind": "branch", "branch_name": "feature/circuits",
                "destination": destination, "source_only": True,
                "remote_url": "https://github.com/SymmetricDevs/Supersymmetry.git",
                "resolved_commit": COMMIT,
            }
            result = {
                "format": "workbench-project-acquisition-result-v3", "plan_id": PLAN_ID,
                "source_kind": "branch", "branch_name": "feature/circuits",
                "destination": destination, "source_only": True,
                "resolved_commit": COMMIT, "receipt_path": str(Path(folder) / "receipt.json"),
                "profile_compatible": False,
                "profile_diagnostic": "pack.toml is missing",
            }
            core.project_acquire_plan = AsyncMock(return_value=plan)
            async def acquire(*args, **kwargs):
                (Path(destination) / "groovy").mkdir(parents=True)
                return result
            core.project_acquire_apply = AsyncMock(side_effect=acquire)
            app = WorkbenchApp(core)
            async with app.run_test(size=(64, 22)) as pilot:
                await self._settle(pilot, lambda: app.view.modules_loaded and app.view.profiles_loaded)
                app.open_project_source()
                await self._settle(pilot, lambda: isinstance(app.screen, ProjectSourceScreen))
                source = app.screen
                source.query_one("#project-source-branch", Input).value = "feature/circuits"
                source.query_one("#project-source-destination", Input).value = destination
                source.query_one("#project-source-name", Input).value = "susy-circuits"
                source.query_one("#project-source-preview", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                core.project_acquire_apply.assert_not_awaited()
                self.assertIn("feature/circuits", app.screen.body)
                self.assertIn(COMMIT, app.screen.body)
                self.assertIn(destination, app.screen.body)
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: isinstance(app.screen, ProjectSourceReadyScreen))
                core.project_acquire_apply.assert_awaited_once_with(
                    plan, "feature/circuits", destination, repository=None,
                    source_only=True,
                )
                core.register_workspace.assert_awaited_once_with(
                    "susy-circuits", destination, make_default=False,
                    expected_record_id="workspaces-before",
                )
                self.assertEqual(destination, app.initial_workspace)
                ready = app.screen
                self.assertIn("review", [row.id for row in ready.query_one(
                    "#project-ready-actions", OptionList).options])
                self.assertIn("required pack paths are absent", str(ready.query_one(
                    "#project-ready-summary", Static).content))

    async def test_ready_screen_retries_workspace_save_without_reacquiring(self) -> None:
        core = source_core()
        result = {
            "destination": "/home/test/Supersymmetry-feature",
            "branch_name": "feature/work", "resolved_commit": COMMIT,
            "profile_compatible": False, "profile_diagnostic": "pack.toml is missing",
        }
        app = WorkbenchApp(core)
        async with app.run_test(size=(64, 22)) as pilot:
            ready = ProjectSourceReadyScreen(
                result, workspace_saved=False, workspace_name="susy-work",
                make_default=False, save_problem="temporary conflict",
                can_review=False, can_axiom=False,
            )
            app.push_screen(ready)
            await self._settle(pilot, lambda: app.screen is ready and
                               bool(ready.query("#project-ready-actions")))
            options = ready.query_one("#project-ready-actions", OptionList)
            self.assertEqual("save", options.get_option_at_index(0).id)
            await pilot.press("enter")
            await self._settle(pilot, lambda: ready.workspace_saved)
            core.register_workspace.assert_awaited_once_with(
                "susy-work", result["destination"], make_default=False,
                expected_record_id="workspaces-before",
            )
            self.assertNotIn("save", [option.id for option in options.options])
            core.project_acquire_apply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
