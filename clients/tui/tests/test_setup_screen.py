"""Headless interaction checks for the setup wizard's execution boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable, Input, OptionList, Select, Static

from workbench_tui.app import PackInstanceScreen, ReviewModal, SetupScreen, WorkbenchApp
from workbench_tui.core_client import CoreClient, CoreClientError
from workbench_tui.keyboard_form import ChoicePicker


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
    core.pack_instance_fresh_provider_status = AsyncMock(return_value={
        "provider": {"status": "configured", "reason": None},
    })
    return core


class SetupScreenInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(30):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

    async def test_home_starts_on_supersymmetry_without_tab(self) -> None:
        core = fake_core("/home/test/workspace")
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={"default": None, "entries": []})
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None
                               and app.view.catalog is not None
                               and isinstance(app.focused, OptionList)
                               and app.focused.id == "home-actions"
                               and app.focused.highlighted == 0)
            await pilot.pause(0.1)
            self.assertEqual(0, app.focused.highlighted)
            self.assertEqual("pack-instance", app.focused.get_option_at_index(0).id)
            self.assertIn("install it in Prism", app.query_one("#home-subtitle", Static).content)
            await pilot.press("down")
            self.assertIn("developer workspace", app.query_one("#home-subtitle", Static).content)
            await pilot.press("up")
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))

    async def test_arrows_and_enter_edit_then_return_to_navigation(self) -> None:
        core = fake_core("/home/test/workspace")
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-mode"))
                               and app.screen.query_one("#setup-mode", Select).has_class("keyboard-selected"))
            screen = app.screen
            self.assertIsNone(app.focused)
            await pilot.press("down", "enter")
            workspace = screen.query_one("#setup-workspace", Input)
            self.assertIs(app.focused, workspace)
            self.assertEqual("setup-workspace", screen._keyboard_editing)
            workspace.value = "/home/test/changed"
            await pilot.press("enter")
            self.assertIsNone(app.focused)
            self.assertTrue(workspace.has_class("keyboard-selected"))
            self.assertEqual("/home/test/changed", workspace.value)
            await pilot.press("enter")
            workspace.value = "/home/test/cancelled"
            await pilot.press("escape")
            self.assertEqual("/home/test/changed", workspace.value)
            self.assertIsNone(app.focused)

    async def test_clean_setup_starts_with_a_runnable_workspace_choice(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        async with app.run_test(size=(100, 30)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-mode"))
                               and app.screen.query_one("#setup-mode", Select).has_class("keyboard-selected"))
            screen = app.screen
            self.assertEqual("review", screen.query_one("#setup-mode", Select).value)
            self.assertFalse(screen.query_one("#setup-profile", Input).display)
            self.assertFalse(screen.query_one("#setup-state-root", Input).display)
            self.assertLess(screen.query_one("#setup-plan", Button).region.y, 30)
            screen.query_one("#setup-more", Button).press()
            await pilot.pause()
            self.assertTrue(screen.query_one("#setup-state-root", Input).display)
            screen.query_one("#setup-mode", Select).value = "full"
            await pilot.pause()
            self.assertTrue(screen.query_one("#setup-profile", Input).display)

    async def test_dependency_table_appears_only_when_core_reports_rows(self) -> None:
        core = fake_core("/home/test/workspace")
        app = WorkbenchApp(core)
        async with app.run_test(size=(58, 24)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.view.setup = None
            app.push_screen(SetupScreen(app.view, initial_workspace="/home/test/workspace"))
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-dependencies")))
            screen = app.screen
            table = screen.query_one("#setup-dependencies", DataTable)
            self.assertFalse(table.display)
            self.assertIn("Choose a workspace, then Check selection.",
                          str(screen.query_one("#setup-status", Static).content))
            core.setup_check.return_value = {
                **core.setup_check.return_value,
                "dependencies": [{"id": "java", "label": "Java", "state": "ready",
                                  "detail": "Installed JDK"}],
            }
            screen.query_one("#setup-check", Button).press()
            await self._settle(pilot, lambda: table.display and table.row_count == 1)

    async def test_explicit_profile_path_starts_in_developer_setup(self) -> None:
        app = WorkbenchApp(
            fake_core("/home/test/workspace"),
            initial_profile_config="/home/test/workbench.toml",
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-mode")))
            screen = app.screen
            self.assertEqual("full", screen.query_one("#setup-mode", Select).value)
            self.assertTrue(screen.query_one("#setup-profile", Input).display)
            self.assertEqual(
                "/home/test/workbench.toml",
                screen.query_one("#setup-profile", Input).value,
            )

    async def test_enter_opens_keyboard_choice_and_returns_to_navigation(self) -> None:
        core = fake_core("/home/test/workspace")
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-mode"))
                               and app.screen.query_one("#setup-mode", Select).has_class("keyboard-selected"))
            screen = app.screen
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, ChoicePicker))
            await pilot.press("down", "enter")
            await self._settle(pilot, lambda: app.screen is screen)
            self.assertEqual("full", screen.query_one("#setup-mode", Select).value)
            self.assertIsNone(app.focused)
            self.assertTrue(screen.query_one("#setup-mode", Select).has_class("keyboard-selected"))

    async def test_click_or_tab_focus_still_accepts_j_and_k_in_paths(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-mode"))
                               and app.screen.query_one("#setup-mode", Select).has_class(
                                   "keyboard-selected"))
            field = app.screen.query_one("#setup-workspace", Input)
            field.value = ""
            field.focus()
            await pilot.press("j", "d", "k", "enter")
            self.assertEqual("jdk", field.value)
            self.assertIsNone(app.focused)

    async def test_review_modal_uses_arrows_and_enter(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        async with app.run_test(size=(110, 38)) as pilot:
            modal = ReviewModal("Check action", "Review the effects", confirm_label="Proceed")
            app.push_screen(modal)
            await self._settle(pilot, lambda: app.screen is modal
                               and bool(modal.query("#review-cancel")))
            self.assertIs(app.focused, modal.query_one("#review-cancel", Button))
            await pilot.press("right")
            self.assertIs(app.focused, modal.query_one("#review-confirm", Button))
            await pilot.press("enter")
            await self._settle(pilot, lambda: app.screen is not modal)

    async def test_review_modal_pages_long_text_without_changing_choice(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        async with app.run_test(size=(58, 24)) as pilot:
            modal = ReviewModal("Read this plan", "\n".join(
                f"Plan detail {index}" for index in range(80)
            ), confirm_label="Apply")
            app.push_screen(modal)
            await self._settle(pilot, lambda: app.screen is modal
                               and bool(modal.query("#review-scroll")))
            scroll = modal.query_one("#review-scroll", VerticalScroll)
            self.assertIs(app.focused, modal.query_one("#review-cancel", Button))
            await pilot.press("pagedown")
            await self._settle(pilot, lambda: scroll.scroll_y > 0)
            self.assertIs(app.focused, modal.query_one("#review-cancel", Button))
            await pilot.press("pageup")
            await self._settle(pilot, lambda: scroll.scroll_y == 0)

    async def test_escape_returns_from_setup_navigation(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and bool(app.screen.query("#setup-mode")))
            await pilot.press("escape")
            await self._settle(pilot, lambda: not isinstance(app.screen, SetupScreen))

    async def test_detected_jdk_fills_setup_java_and_is_checked_in_plan(self) -> None:
        core = fake_core("/home/test/workspace")
        core.java_inventory = AsyncMock(return_value={
            "format": "workbench-java-inventory-v1", "candidates": [
                {"state": "available", "jdk": True, "feature_version": 25,
                 "probe": {"java_home": "/usr/lib/jvm/jdk-25"}},
                {"state": "unavailable", "jdk": False, "feature_version": None,
                 "probe": None},
            ],
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.setup is not None)
            app.open_setup()
            await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                               and app.screen.detected_java.get("installed-0") == "/usr/lib/jvm/jdk-25")
            screen = app.screen
            core.java_inventory.assert_awaited_with()
            screen.query_one("#setup-mode", Select).value = "full"
            screen.query_one("#setup-more", Button).press()
            await pilot.pause()
            self.assertFalse(screen.query_one("#setup-java-candidates", Select).disabled)
            self.assertIn("Core checks", str(screen.query_one("#setup-java-hint", Static).render()))
            screen.query_one("#setup-profile", Input).value = "/home/test/workbench.toml"
            screen.query_one("#setup-java-candidates", Select).value = "installed-0"
            await self._settle(pilot, lambda: screen.query_one("#setup-java", Input).value == "/usr/lib/jvm/jdk-25")
            screen.query_one("#setup-plan", Button).press()
            await self._settle(pilot, lambda: core.setup_plan.await_count == 1)
            self.assertIn("--java-home", core.setup_plan.await_args.args[0])
            self.assertIn("/usr/lib/jvm/jdk-25", core.setup_plan.await_args.args[0])

    async def test_empty_or_failed_discovery_keeps_setup_path_editable(self) -> None:
        for response in (
            {"format": "workbench-java-inventory-v1", "candidates": []},
            CoreClientError("inventory unavailable"),
        ):
            with self.subTest(response=response):
                core = fake_core("/home/test/workspace")
                core.java_inventory = AsyncMock(
                    side_effect=response if isinstance(response, Exception) else None,
                    return_value=response if isinstance(response, dict) else None,
                )
                app = WorkbenchApp(core)
                async with app.run_test(size=(110, 38)) as pilot:
                    await self._settle(pilot, lambda: app.view.setup is not None)
                    app.open_setup()
                    await self._settle(pilot, lambda: isinstance(app.screen, SetupScreen)
                                       and core.java_inventory.await_count == 1)
                    screen = app.screen
                    screen.query_one("#setup-mode", Select).value = "full"
                    screen.query_one("#setup-more", Button).press()
                    await pilot.pause()
                    await self._settle(pilot, lambda: "JDKs" in str(
                        screen.query_one("#setup-java-hint", Static).render()
                    ))
                    self.assertTrue(screen.query_one("#setup-java-candidates", Select).disabled)
                    self.assertFalse(screen.query_one("#setup-java", Input).disabled)
                    self.assertFalse(bool(screen.query("#setup-java-list")))
                    screen.query_one("#setup-profile", Input).value = "/home/test/workbench.toml"
                    screen.query_one("#setup-java", Input).value = "/my/java"
                    screen.query_one("#setup-plan", Button).press()
                    await self._settle(pilot, lambda: core.setup_plan.await_count == 1)
                    self.assertIn("/my/java", core.setup_plan.await_args.args[0])

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
