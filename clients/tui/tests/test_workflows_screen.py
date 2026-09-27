"""Headless checks for the prototype's two catalog presentation paths."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock

from textual.widgets import Button, Input, OptionList

from workbench_tui.app import (
    CatalogInputsScreen,
    CatalogValueScreen,
    ResultScreen,
    ReviewModal,
    WorkbenchApp,
    WorkflowsScreen,
    _runnable_catalog_action,
)
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

    async def test_read_only_action_collects_required_input_before_review(self) -> None:
        core = catalog_core()
        action = {
            "command_id": "atlas.recipes-context",
            "title": "Open captured recipes",
            "summary": "Inspect one exact captured recipe graph",
            "suite_id": "atlas",
            "authority": "Atlas",
            "risk": "read-only",
            "preview": "none",
            "availability": "experimental",
            "action_digest": DIGEST,
            "options": [{
                "key": "path", "label": "Recipe graph", "help": "Select an existing graph.",
                "kind": "path", "required": True, "nargs": "one", "repeat": False,
                "required_group": False,
            }],
            "document": None,
        }
        core.catalog.return_value["commands"].append(action)
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
            self.assertEqual({item["command_id"] for item in screen._actions()}, {
                "environment.status", "manuals.overview", "atlas.recipes-context",
            })
            self.assertEqual(app.focused.id, "workflow-list")
            await pilot.press("/")
            self.assertEqual(app.focused.id, "workflow-search")
            await pilot.press("escape")
            self.assertEqual(app.focused.id, "workflow-list")
            screen._select("atlas.recipes-context")
            screen.query_one("#workflow-run", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, CatalogInputsScreen))
            form = app.screen
            form._submit()
            self.assertIn("Recipe graph", str(form.query_one("#catalog-inputs-error").render()))
            fields = form.query_one("#catalog-fields", OptionList)
            fields.highlighted = 0
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, CatalogValueScreen))
            value_input = app.screen.query_one("#catalog-value-input", Input)
            value_input.value = "/tmp/recipe-graph.sqlite"
            await pilot.press("enter")
            await self._settle(pilot, lambda: app.screen is form)
            form._submit()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
            self.assertEqual(core.command_review.await_args.args[2], {
                "path": "/tmp/recipe-graph.sqlite",
            })
            core.run_reviewed_command.assert_not_awaited()
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: isinstance(app.screen, ResultScreen))
            self.assertEqual(core.run_reviewed_command.await_args.args[2], {
                "path": "/tmp/recipe-graph.sqlite",
            })

    async def test_escape_returns_from_actions_to_home(self) -> None:
        app = WorkbenchApp(catalog_core())
        async with app.run_test(size=(100, 35)) as pilot:
            await self._settle(pilot, lambda: app.view.catalog is not None)
            app.open_workflows()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkflowsScreen)
                               and bool(app.screen.query("#workflow-list")))
            await pilot.press("escape")
            self.assertNotIsInstance(app.screen, WorkflowsScreen)


class CatalogAdmissionTests(unittest.TestCase):
    def test_unsupported_catalog_entries_are_not_offered_to_run(self) -> None:
        base = {
            "risk": "read-only", "preview": "none", "availability": "experimental",
            "document": None, "options": [],
        }
        self.assertTrue(_runnable_catalog_action(base))
        self.assertFalse(_runnable_catalog_action({**base, "risk": "mutating"}))
        self.assertFalse(_runnable_catalog_action({**base, "preview": "inert-only"}))
        self.assertFalse(_runnable_catalog_action({**base, "availability": "unavailable"}))
        self.assertFalse(_runnable_catalog_action({**base, "options": [{
            "key": "paths", "kind": "path", "nargs": "one", "repeat": True,
        }]}))
        self.assertTrue(_runnable_catalog_action({**base, "options": [{
            "key": "path", "kind": "path", "nargs": "one", "repeat": False,
        }]}))
