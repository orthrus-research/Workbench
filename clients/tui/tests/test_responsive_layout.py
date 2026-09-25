"""Compact terminal sizes keep the wizard and catalog usable."""

from pathlib import Path
import tempfile
import unittest

from textual.widgets import DataTable

from workbench_tui.app import (
    ModulesScreen,
    ReviewModal,
    SetupScreen,
    WorkbenchApp,
    WorkflowsScreen,
)
from workbench_tui.core_client import CoreClientError


class _UnavailableCore:
    async def version(self):
        raise CoreClientError("test Core is unavailable")


class ResponsiveLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_secondary_screens_fit_narrow_terminals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for width, height in ((40, 18), (58, 24)):
                with self.subTest(size=(width, height)):
                    app = WorkbenchApp(
                        _UnavailableCore(),
                        preference_path=Path(directory) / f"{width}.json",
                    )
                    async with app.run_test(size=(width, height)) as pilot:
                        app.view.modules = [
                            {"id": f"module-{index:02d}", "version": "1", "state": "available"}
                            for index in range(10)
                        ]
                        app.view.profiles = []
                        app.view.catalog = {
                            "commands": [
                                {
                                    "command_id": f"action-{index:02d}",
                                    "title": f"Action {index}",
                                    "summary": "Show environment data",
                                    "suite_id": "core",
                                }
                                for index in range(10)
                            ]
                        }
                        app.view.setup = {
                            "selection": {"workspace": directory, "profile_config": ""},
                            "dependencies": [],
                            "configured": False,
                        }

                        app.push_screen(ModulesScreen(app.view))
                        await pilot.pause()
                        self.assertTrue(app.screen.has_class("-narrow"))
                        table = app.screen.query_one("#modules-table", DataTable)
                        detail = app.screen.query_one("#module-detail")
                        self.assertGreaterEqual(table.region.height, 4)
                        self.assertGreaterEqual(detail.region.width, width - 8)
                        self.assertLess(app.screen.query_one("#modules-back").region.y, height - 1)
                        table.focus()
                        await pilot.press("down")
                        await pilot.pause()
                        self.assertIn("module-01", detail.content.plain)
                        if width == 40:
                            await pilot.resize_terminal(80, 24)
                            await pilot.pause()
                            self.assertTrue(app.screen.has_class("-wide"))
                            self.assertGreater(detail.region.x, table.region.x)
                            await pilot.resize_terminal(width, height)
                            await pilot.pause()
                            self.assertTrue(app.screen.has_class("-narrow"))
                        app.pop_screen()
                        await pilot.pause()

                        app.push_screen(WorkflowsScreen(app.view))
                        await pilot.pause()
                        listing = app.screen.query_one("#workflow-list")
                        detail_panel = app.screen.query_one("#workflow-detail-panel")
                        self.assertGreaterEqual(listing.region.height, 4)
                        self.assertGreaterEqual(detail_panel.region.width, width - 8)
                        self.assertLess(app.screen.query_one("#workflow-back").region.y, height - 1)
                        listing.focus()
                        await pilot.press("down")
                        await pilot.pause()
                        self.assertEqual("action-00", app.screen.selected["command_id"])
                        app.pop_screen()
                        await pilot.pause()

                        app.push_screen(SetupScreen(app.view))
                        await pilot.pause()
                        self.assertLess(app.screen.query_one("#setup-mode").region.y, height - 1)
                        self.assertLess(app.screen.query_one("#setup-workspace").region.y, height - 1)
                        plan_button = app.screen.query_one("#setup-plan")
                        plan_button.scroll_visible(animate=False, immediate=True)
                        await pilot.pause()
                        self.assertLess(plan_button.region.y, height - 1)
                        self.assertGreaterEqual(plan_button.region.y, 1)

                        app.push_screen(
                            ReviewModal(
                                "Apply setup?",
                                "Selected paths\n" + "workspace: /a/long/path\n" * 10,
                                confirm_label="Apply exact plan",
                            )
                        )
                        await pilot.pause()
                        self.assertLess(
                            app.screen.query_one("#review-confirm").region.bottom,
                            height,
                        )


if __name__ == "__main__":
    unittest.main()
