"""The landing screen keeps the first action visible at common terminal sizes."""

import unittest

from textual.widgets import OptionList, Static

from workbench_tui.app import EnvironmentView, WorkbenchApp
from workbench_tui.core_client import CoreClientError


class _UnavailableCore:
    async def version(self):
        raise CoreClientError("test Core is unavailable")


class BrandingTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_palette_is_black_grey_and_white(self) -> None:
        app = WorkbenchApp(_UnavailableCore())
        theme = app.get_theme("workbench-dark")
        self.assertEqual("#f2f2f2", theme.primary)
        self.assertEqual(theme.primary, theme.accent)
        for name in (
            "primary", "secondary", "accent", "foreground", "background",
            "surface", "panel", "warning", "error", "success",
        ):
            value = getattr(theme, name)
            self.assertEqual({value[1:3], value[3:5], value[5:7]}, {value[1:3]}, name)

    async def test_welcome_and_primary_action_fit_without_empty_banner(self) -> None:
        app = WorkbenchApp(_UnavailableCore())
        async with app.run_test(size=(100, 30)):
            home = app.screen_stack[0]
            welcome = home.query_one("#home-welcome")
            actions = home.query_one("#actions-panel")
            environment = home.query_one("#environment-panel")
            self.assertLessEqual(welcome.size.height, 5)
            self.assertEqual("Welcome to Workbench", home.query_one("#home-title", Static).content)
            self.assertGreater(actions.size.width, environment.size.width)
            self.assertEqual(
                "pack-instance",
                home.query_one("#home-actions", OptionList).get_option_at_index(0).id,
            )

    async def test_narrow_terminal_stacks_full_width_actions_first(self) -> None:
        app = WorkbenchApp(_UnavailableCore())
        async with app.run_test(size=(58, 24)) as pilot:
            home = app.screen_stack[0]
            self.assertTrue(home.has_class("narrow-brand"))
            panels = home.query_one("#home-panels")
            actions = home.query_one("#actions-panel")
            environment = home.query_one("#environment-panel")
            self.assertIs(actions, panels.children[0])
            self.assertGreaterEqual(actions.size.width, 50)
            await pilot.resize_terminal(40, 18)
            await pilot.pause()
            self.assertGreaterEqual(
                home.query_one("#home-actions").size.width,
                len("Set up developer environment") + 4,
            )
            self.assertIs(actions, panels.children[0])
            await pilot.resize_terminal(100, 34)
            await pilot.pause()
            self.assertFalse(home.has_class("narrow-brand"))
            self.assertIs(actions, panels.children[0])
            self.assertGreater(actions.size.width, environment.size.width)

    async def test_review_only_home_does_not_present_developer_tools_as_blockers(self) -> None:
        app = WorkbenchApp(_UnavailableCore())
        async with app.run_test(size=(100, 30)) as pilot:
            app.view = EnvironmentView(
                version={"version": "test"},
                setup={
                    "configured": True,
                    "state": "attention",
                    "selection": {"profile_config": None},
                    "blockers": ["pixi"],
                    "dependencies": [{"id": "pixi", "label": "Pixi", "detail": "install it"}],
                },
            )
            app._render_home()
            await pilot.pause()
            home = app.screen_stack[0]
            self.assertIn("Saved", str(home.query_one("#environment-summary", Static).content))
            details = str(home.query_one("#environment-dependencies", Static).content)
            self.assertIn("Review mode is ready", details)
            self.assertNotIn("Pixi", details)
