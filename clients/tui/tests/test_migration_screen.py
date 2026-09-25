"""Headless confirmation and conflict checks for legacy user configuration import."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from textual.widgets import OptionList

from test_setup_screen import fake_core
from workbench_tui.app import ResultScreen, ReviewModal, WorkbenchApp
from workbench_tui.core_client import CoreClientError


def migration_record(root: Path, *, state: str = "ready") -> dict:
    file_state = {"ready": "copy", "conflict": "conflict", "imported": "copied"}[state]
    return {
        "format": "workbench-user-config-migration-v1",
        "schema_version": 1,
        "source": str(root / "earlier"),
        "destination": str(root / "stable"),
        "files": [{
            "name": "setup-v1.json",
            "sha256": "sha256:" + "a" * 64,
            "state": file_state,
        }],
        "state": state,
    }


class MigrationInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(30):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected migration state")

    async def _open_home_migration(self, app: WorkbenchApp, pilot) -> None:
        await self._settle(pilot, lambda: app.view.version is not None)
        actions = app.screen_stack[0].query_one("#home-actions", OptionList)
        index = next(
            index for index in range(actions.option_count)
            if actions.get_option_at_index(index).id == "migrate"
        )
        self.assertFalse(actions.get_option_at_index(index).disabled)
        actions.focus()
        actions.highlighted = index
        await pilot.press("enter")

    async def test_ready_import_requires_confirmation_then_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = fake_core(directory)
            preview = migration_record(root)
            core.migration_preview = AsyncMock(return_value=preview)
            core.migration_import = AsyncMock(return_value=migration_record(root, state="imported"))
            app = WorkbenchApp(core, initial_workspace=directory)
            async with app.run_test(size=(110, 38)) as pilot:
                await self._open_home_migration(app, pilot)
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                self.assertIn("setup-v1.json: copy", app.screen.body)
                core.migration_import.assert_not_awaited()

                await pilot.click("#review-cancel")
                await self._settle(pilot, lambda: not app._migration_busy)
                core.migration_import.assert_not_awaited()

                await self._open_home_migration(app, pilot)
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: isinstance(app.screen, ResultScreen))
                core.migration_import.assert_awaited_once_with(preview)
                self.assertIn("setup-v1.json: copied", app.screen.output)
                await self._settle(pilot, lambda: core.version.await_count >= 2)

    async def test_conflict_blocks_bulk_import_and_shows_both_homes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = fake_core(directory)
            core.migration_preview = AsyncMock(return_value=migration_record(root, state="conflict"))
            core.migration_import = AsyncMock()
            app = WorkbenchApp(core, initial_workspace=directory)
            async with app.run_test() as pilot:
                await self._open_home_migration(app, pilot)
                await self._settle(pilot, lambda: isinstance(app.screen, ResultScreen))
                self.assertIn(str(root / "earlier"), app.screen.output)
                self.assertIn(str(root / "stable"), app.screen.output)
                self.assertIn("resolve the conflict", app.screen.output)
                core.migration_import.assert_not_awaited()

    async def test_preview_error_is_actionable_and_does_not_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = fake_core(directory)
            core.migration_preview = AsyncMock(side_effect=CoreClientError("invalid earlier setup record"))
            core.migration_import = AsyncMock()
            app = WorkbenchApp(core, initial_workspace=directory)
            async with app.run_test() as pilot:
                await self._open_home_migration(app, pilot)
                await self._settle(pilot, lambda: isinstance(app.screen, ResultScreen))
                self.assertIn("invalid earlier setup record", app.screen.output)
                self.assertIn("try again", app.screen.output)
                core.migration_import.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
