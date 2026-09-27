"""Supersymmetry setup can choose local inputs from a keyboard-driven tree."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from textual.widgets import Button, Input

from test_setup_screen import fake_core
from workbench_tui.app import (
    InstancePathPicker, PackInstanceScreen, WorkbenchApp, _InstancePathTree,
)


class InstancePathPickerTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(80):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("The path tree did not reach the expected state")

    @staticmethod
    def _screen(app: WorkbenchApp) -> PackInstanceScreen:
        screen = PackInstanceScreen(
            {"choice": {"record_id": "choice", "source_plan_id": None},
             "source_state": "none"},
            {"default": "dev", "entries": [
                {"name": "dev", "path": "/home/test/workspace"},
            ]},
        )
        app.push_screen(screen)
        return screen

    async def test_zip_tree_filters_other_files_and_enter_fills_zip_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "complete-instance.ZIP"
            archive.write_bytes(b"zip test fixture")
            (root / "notes.txt").write_text("not an archive")
            app = WorkbenchApp(fake_core(str(root)))
            async with app.run_test(size=(110, 38)) as pilot:
                screen = self._screen(app)
                await self._settle(pilot, lambda: app.screen is screen
                                   and bool(screen.query("#pack-zip-path")))
                screen.query_one("#pack-zip-path", Input).value = str(root)
                screen.query_one("#pack-zip-browse", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, InstancePathPicker))
                picker = app.screen
                tree = picker.query_one("#instance-path-tree", _InstancePathTree)
                await self._settle(pilot, lambda: len(tree.root.children) == 1)
                self.assertEqual(archive, tree.root.children[0].data.path)
                tree.focus()
                await pilot.press("down", "enter")
                await self._settle(pilot, lambda: app.screen is screen
                                   and bool(screen.query("#pack-prism-root")))
                self.assertEqual(str(archive), screen.query_one("#pack-zip-path", Input).value)

    async def test_folder_tree_expands_and_enter_fills_existing_prism_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outer = root / "launcher"
            target = outer / "PrismLauncher"
            target.mkdir(parents=True)
            app = WorkbenchApp(fake_core(str(root)))
            async with app.run_test(size=(110, 38)) as pilot:
                screen = self._screen(app)
                await self._settle(pilot, lambda: app.screen is screen
                                   and bool(screen.query("#pack-prism-root")))
                screen.query_one("#pack-prism-root", Input).value = str(root)
                screen.query_one("#pack-prism-browse", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, InstancePathPicker))
                picker = app.screen
                tree = picker.query_one("#instance-path-tree", _InstancePathTree)
                await self._settle(pilot, lambda: len(tree.root.children) == 1)
                tree.focus()
                await pilot.press("down", "space")
                await self._settle(pilot, lambda: len(tree.root.children[0].children) == 1)
                await pilot.press("down", "backspace")
                await self._settle(pilot, lambda: tree.path == outer
                                   and len(tree.root.children) == 1)
                await pilot.press("down", "enter")
                await self._settle(pilot, lambda: app.screen is screen)
                self.assertEqual(str(target), screen.query_one("#pack-prism-root", Input).value)

    async def test_escape_leaves_manually_entered_new_prism_path_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            new_path = str(Path(temporary) / "new-PrismLauncher")
            app = WorkbenchApp(fake_core(temporary))
            async with app.run_test(size=(110, 38)) as pilot:
                screen = self._screen(app)
                await self._settle(pilot, lambda: app.screen is screen
                                   and bool(screen.query("#pack-prism-root")))
                screen.query_one("#pack-prism-root", Input).value = new_path
                screen.query_one("#pack-prism-browse", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, InstancePathPicker))
                await pilot.press("escape")
                await self._settle(pilot, lambda: app.screen is screen)
                self.assertEqual(new_path, screen.query_one("#pack-prism-root", Input).value)

    async def test_go_to_folder_uses_an_absolute_path_without_tab(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            other = root / "other-drive"
            other.mkdir()
            archive = other / "custom.zip"
            archive.write_bytes(b"zip test fixture")
            app = WorkbenchApp(fake_core(str(root)))
            async with app.run_test(size=(110, 38)) as pilot:
                screen = self._screen(app)
                await self._settle(pilot, lambda: app.screen is screen
                                   and bool(screen.query("#pack-zip-path")))
                screen.query_one("#pack-zip-path", Input).value = str(root)
                screen.query_one("#pack-zip-browse", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, InstancePathPicker))
                picker = app.screen
                tree = picker.query_one("#instance-path-tree", _InstancePathTree)
                await pilot.press("ctrl+g")
                location = picker.query_one("#instance-path-location", Input)
                self.assertIs(app.focused, location)
                location.value = str(other)
                await pilot.press("enter")
                await self._settle(pilot, lambda: tree.path == other
                                   and len(tree.root.children) == 1)
                self.assertIs(app.focused, tree)
                await pilot.press("down", "enter")
                await self._settle(pilot, lambda: app.screen is screen)
                self.assertEqual(str(archive), screen.query_one("#pack-zip-path", Input).value)

    async def test_zip_tree_remains_keyboard_usable_in_a_narrow_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "complete.zip"
            archive.write_bytes(b"zip test fixture")
            app = WorkbenchApp(fake_core(str(root)))
            async with app.run_test(size=(40, 18)) as pilot:
                screen = self._screen(app)
                await self._settle(pilot, lambda: app.screen is screen
                                   and bool(screen.query("#pack-zip-path")))
                screen.query_one("#pack-zip-path", Input).value = str(root)
                screen.query_one("#pack-zip-browse", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, InstancePathPicker))
                picker = app.screen
                tree = picker.query_one("#instance-path-tree", _InstancePathTree)
                await self._settle(pilot, lambda: len(tree.root.children) == 1)
                self.assertGreaterEqual(tree.region.height, 3)
                await pilot.press("down", "enter")
                await self._settle(pilot, lambda: app.screen is screen)
                self.assertEqual(str(archive), screen.query_one("#pack-zip-path", Input).value)


if __name__ == "__main__":
    unittest.main()
