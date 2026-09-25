"""Headless checks for the prototype's two catalog presentation paths."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock

from textual.widgets import Button

from workbench_tui.app import ResultScreen, ReviewModal, WorkbenchApp, WorkflowsScreen
from workbench_tui.core_client import CommandOutput, CoreClient


DIGEST = "sha256:" + "a" * 64


def catalog_core() -> Mock:
    core = Mock(spec=CoreClient)
    core.version = AsyncMock(return_value={"component_id": "workbench-core", "version": "test"})
    core.environment_resolve = AsyncMock(return_value={
        "format": "workbench-environment-resolution-v1",
        "resolution_id": "workbench-environment-resolution:sha256:" + "a" * 64,
        "workspace": {"path": "/tmp/workbench", "source": "user-workspaces"},
    })
    core.setup_check = AsyncMock(return_value={"format": "workbench-setup-check-v2", "selection": {}, "dependencies": [], "blockers": []})
    core.modules = AsyncMock(return_value=[])
    core.profiles = AsyncMock(return_value=[])
    core.catalog = AsyncMock(return_value={
        "catalog_digest": DIGEST,
        "commands": [
            {"command_id": "environment.status", "title": "Environment status", "summary": "Inspect setup", "suite_id": "shell", "authority": "Shell", "risk": "read-only", "preview": "none", "availability": "available", "action_digest": DIGEST, "options": [], "document": None},
            {"command_id": "manuals.overview", "title": "Manuals", "summary": "Read the guide", "suite_id": "manuals", "authority": "Manuals", "risk": "read-only", "preview": "none", "availability": "available", "action_digest": DIGEST, "options": [], "document": "modules/manuals/README.md"},
            {"command_id": "unsafe.write", "title": "Write", "summary": "Mutation", "suite_id": "shell", "authority": "Shell", "risk": "writes", "preview": "none", "availability": "available", "action_digest": DIGEST, "options": [], "document": None},
        ],
    })
    core.command_review = AsyncMock(return_value={"risk": "read-only", "review_digest": DIGEST, "execute_command": "workbench environment status"})
    core.run_reviewed_command = AsyncMock(return_value=CommandOutput((), 0, "status output", ""))
    core.open_document = AsyncMock(return_value=CommandOutput((), 0, "manual text", ""))
    return core


class WorkflowInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(40):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

    async def test_executable_action_needs_separate_review_and_document_does_not(self) -> None:
        core = catalog_core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(100, 35)) as pilot:
            await self._settle(pilot, lambda: app.view.catalog is not None)
            app.open_workflows()
            await self._settle(
                pilot,
                lambda: isinstance(app.screen, WorkflowsScreen)
                and bool(app.screen.query("#workflow-detail")),
            )
            screen = app.screen

            screen._select("unsafe.write")
            self.assertTrue(screen.query_one("#workflow-run", Button).disabled)
            screen._select("environment.status")
            self.assertFalse(screen.query_one("#workflow-run", Button).disabled)
            screen.query_one("#workflow-run", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
            core.run_reviewed_command.assert_not_awaited()
            await pilot.click("#review-cancel")
            await self._settle(pilot, lambda: app.screen is screen)
            core.run_reviewed_command.assert_not_awaited()

            screen._select("manuals.overview")
            self.assertEqual(str(screen.query_one("#workflow-run", Button).label), "Open document")
            screen.query_one("#workflow-run", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ResultScreen))
            core.open_document.assert_awaited_once()
            core.run_reviewed_command.assert_not_awaited()
