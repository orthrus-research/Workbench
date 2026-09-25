"""Headless interaction checks for the setup wizard's execution boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from textual.widgets import Button, Input

from workbench_tui.app import ReviewModal, SetupScreen, WorkbenchApp
from workbench_tui.core_client import CoreClient


PLAN_ID = "workbench-setup-plan-" + "a" * 64


def fake_core(workspace: str, *, blockers: list[str] | None = None) -> Mock:
    core = Mock(spec=CoreClient)
    core.version = AsyncMock(return_value={"component_id": "workbench-core", "version": "0.1.test"})
    core.environment_resolve = AsyncMock(return_value={
        "format": "workbench-environment-resolution-v1",
        "resolution_id": "workbench-environment-resolution:sha256:" + "a" * 64,
        "workspace": {"path": workspace, "source": "argument"},
    })
    core.setup_check = AsyncMock(
        return_value={
            "format": "workbench-setup-check-v2",
            "state": "needs-setup",
            "selection": {"workspace": workspace, "profile_config": ""},
            "dependencies": [],
            "blockers": [],
        }
    )
    core.modules = AsyncMock(return_value=[])
    core.profiles = AsyncMock(return_value=[])
    core.catalog = AsyncMock(return_value={"commands": []})
    core.workspace_home = AsyncMock(return_value={"workspace": {}, "status": {}})
    core.setup_plan = AsyncMock(
        return_value={
            "format": "workbench-setup-plan-v2",
            "plan_id": PLAN_ID,
            "state": "blocked" if blockers else "ready",
            "selection": {"workspace": workspace},
            "actions": [{"id": "save-selection", "effect": "Save the reviewed selection"}],
            "dependencies": [],
            "blockers": blockers or [],
        }
    )
    core.setup_apply = AsyncMock(
        return_value={
            "applied_plan_id": PLAN_ID,
            "record": {"selection": {"workspace": workspace}},
        }
    )
    core.java_inventory = AsyncMock(return_value={"format": "workbench-java-inventory-v1"})
    return core


class SetupScreenInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(30):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

    async def test_apply_requires_a_current_plan_and_separate_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = str(Path(directory) / "source workspace")
            core = fake_core(workspace)
            app = WorkbenchApp(core, initial_workspace=workspace)
            async with app.run_test(size=(110, 38)) as pilot:
                await self._settle(pilot, lambda: app.view.version is not None)
                app.open_setup()
                await self._settle(
                    pilot,
                    lambda: isinstance(app.screen, SetupScreen)
                    and bool(app.screen.query("#setup-apply")),
                )
                screen = app.screen
                self.assertTrue(screen.query_one("#setup-apply", Button).disabled)

                screen.query_one("#setup-profile", Input).value = str(
                    Path(directory) / "workbench.toml"
                )
                screen.query_one("#setup-plan", Button).press()
                await self._settle(pilot, lambda: screen.plan is not None)
                self.assertFalse(screen.query_one("#setup-apply", Button).disabled)
                core.setup_apply.assert_not_awaited()

                screen.query_one("#setup-apply", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                core.setup_apply.assert_not_awaited()
                await pilot.click("#review-cancel")
                await self._settle(pilot, lambda: app.screen is screen)
                core.setup_apply.assert_not_awaited()

                screen.query_one("#setup-workspace", Input).value = workspace + " changed"
                await pilot.pause(0.05)
                self.assertIsNone(screen.plan)
                self.assertTrue(screen.query_one("#setup-apply", Button).disabled)

                screen.query_one("#setup-plan", Button).press()
                await self._settle(pilot, lambda: screen.plan is not None)
                screen.query_one("#setup-apply", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: core.setup_apply.await_count == 1)
                args = core.setup_apply.await_args.args
                self.assertEqual(args[0], PLAN_ID)
                self.assertEqual(args[1], core.setup_plan.await_args.args[0])
                self.assertIn(workspace + " changed", args[1])

    async def test_landing_uses_named_default_without_changing_setup_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            saved = str(Path(directory) / "saved setup")
            default = str(Path(directory) / "named default")
            core = fake_core(saved)
            core.environment_resolve.return_value["workspace"] = {
                "path": default, "source": "user-workspaces"
            }
            app = WorkbenchApp(core)
            async with app.run_test() as pilot:
                await self._settle(pilot, lambda: app.view.setup is not None)
                self.assertEqual(default, app.view.workspace)
                self.assertEqual(saved, app.view.setup["selection"]["workspace"])

    async def test_blocked_core_plan_cannot_be_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = str(Path(directory) / "source")
            core = fake_core(workspace, blockers=["Java 21 is unavailable"])
            app = WorkbenchApp(core, initial_workspace=workspace)
            async with app.run_test(size=(110, 38)) as pilot:
                await self._settle(pilot, lambda: app.view.version is not None)
                app.open_setup()
                await self._settle(
                    pilot,
                    lambda: isinstance(app.screen, SetupScreen)
                    and bool(app.screen.query("#setup-apply")),
                )
                screen = app.screen
                screen.query_one("#setup-profile", Input).value = str(
                    Path(directory) / "workbench.toml"
                )
                screen.query_one("#setup-plan", Button).press()
                await self._settle(pilot, lambda: screen.plan is not None)
                self.assertTrue(screen.query_one("#setup-apply", Button).disabled)
                core.setup_apply.assert_not_awaited()

    async def test_plan_response_is_discarded_after_selection_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = str(Path(directory) / "source")
            core = fake_core(workspace)
            release_plan = asyncio.Event()
            plan_record = core.setup_plan.return_value

            async def delayed_plan(options):
                await release_plan.wait()
                return plan_record

            core.setup_plan = AsyncMock(side_effect=delayed_plan)
            app = WorkbenchApp(core, initial_workspace=workspace)
            async with app.run_test(size=(110, 38)) as pilot:
                await self._settle(pilot, lambda: app.view.version is not None)
                app.open_setup()
                await self._settle(
                    pilot,
                    lambda: isinstance(app.screen, SetupScreen)
                    and bool(app.screen.query("#setup-plan")),
                )
                screen = app.screen
                screen.query_one("#setup-profile", Input).value = str(
                    Path(directory) / "workbench.toml"
                )
                screen.query_one("#setup-plan", Button).press()
                await self._settle(pilot, lambda: core.setup_plan.await_count == 1)

                screen.query_one("#setup-workspace", Input).value = workspace + " changed"
                await pilot.pause()
                release_plan.set()
                await self._settle(pilot, lambda: not screen.busy)

                self.assertIsNone(screen.plan)
                self.assertEqual((), screen.plan_options)
                self.assertTrue(screen.query_one("#setup-apply", Button).disabled)
                self.assertIn("Selection changed", str(screen.query_one("#setup-status").render()))
                core.setup_apply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
