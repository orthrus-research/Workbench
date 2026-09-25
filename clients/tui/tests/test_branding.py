"""The Workbench mark remains legible without hiding the landing actions."""

import unittest
from unittest.mock import patch

from rich.cells import cell_len
from textual.screen import Screen
from textual.widgets import Static

from workbench_tui.app import WorkbenchApp
from workbench_tui.brand import COMPACT_MARK, MARK
from workbench_tui.core_client import CoreClientError


class _UnavailableCore:
    async def version(self):
        raise CoreClientError("test Core is unavailable")


class BrandingTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_palette_is_black_grey_and_white(self) -> None:
        app = WorkbenchApp(_UnavailableCore())
        theme = app.get_theme("workbench-dark")
        self.assertEqual("#ffffff", theme.primary)
        self.assertEqual(theme.primary, theme.accent)
        for name in (
            "primary", "secondary", "accent", "foreground", "background",
            "surface", "panel", "warning", "error", "success",
        ):
            value = getattr(theme, name)
            self.assertEqual({value[1:3], value[3:5], value[5:7]}, {value[1:3]}, name)

    async def test_logo_adapts_to_terminal_size(self) -> None:
        self.assertTrue(all(cell_len(line) == 20 for line in MARK))
        self.assertTrue(all(cell_len(line) == 14 for line in COMPACT_MARK))
        app = WorkbenchApp(_UnavailableCore())
        async with app.run_test(size=(100, 34)) as pilot:
            home = app.screen_stack[0]
            logo = home.query_one("#brand-logo", Static)
            self.assertEqual("\n".join(MARK), logo.content.plain)
            self.assertFalse(home.has_class("compact-brand"))
            app.push_screen(Screen())
            await pilot.pause()
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertTrue(home.has_class("compact-brand"))
            self.assertEqual("\n".join(COMPACT_MARK), logo.content.plain)
            self.assertEqual(14, logo.size.width)
            self.assertTrue(home.query_one("#home-actions").visible)

    async def test_plain_wordmark_remains_when_blocks_cannot_be_encoded(self) -> None:
        with patch("workbench_tui.app._supports_block_logo", return_value=False):
            app = WorkbenchApp(_UnavailableCore())
        async with app.run_test(size=(80, 24)):
            home = app.screen_stack[0]
            self.assertTrue(home.has_class("plain-brand"))
            self.assertFalse(home.query_one("#brand-logo").display)
            self.assertEqual("WORKBENCH", home.query_one("#hero", Static).content)

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
                len("Set up or repair environment") + 4,
            )
            self.assertIs(actions, panels.children[0])
            await pilot.resize_terminal(100, 34)
            await pilot.pause()
            self.assertFalse(home.has_class("narrow-brand"))
            self.assertIs(environment, panels.children[0])
