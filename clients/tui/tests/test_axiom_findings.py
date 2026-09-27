"""Retained Axiom findings remain reachable through the compact TUI."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from textual.widgets import Button, OptionList, RichLog, Static

from test_workflows_screen import catalog_core
from workbench_tui.app import WorkbenchApp
from workbench_tui.axiom_findings import AxiomFindingDetailScreen, AxiomFindingsScreen
from workbench_tui.core_client import CoreClient, CoreClientError


SESSION = "work-session-v2-" + "a" * 32
REVISION = "check-diagnostics:sha256:" + "b" * 64
ATTEMPT = "attempt-1"


def _finding(number: int) -> dict:
    return {
        "id": f"diagnostic-{number}", "severity": "error" if number == 129 else "warning",
        "message": f"Native registry diagnostic {number}",
        "location": {"path": "groovy/classes/Registry.groovy", "line": 42, "column": 7}
        if number == 129 else None,
        "side": "candidate", "channel": "groovy-log",
    }


def _page(offset: int, *, group: str | None = None) -> dict:
    numbers = ([129] if group == "error-located" else
               list(range(128)) if offset == 0 else list(range(128, 130)))
    findings = [_finding(number) for number in numbers]
    return {
        "format": "workbench-developer-action-v1", "exit_code": 0,
        "result": {
            "format": "workbench-material-diagnostic-view-v1", "id": REVISION,
            "native_outcome": "native-failed", "findings_count": 130,
            "finding_counts": {
                "error-located": 1, "error-unlocated": 0,
                "warning-located": 0, "warning-unlocated": 129,
                "information-located": 0, "information-unlocated": 0,
            },
            "group": group, "group_count": 1 if group else 130,
            "offset": offset, "next_offset": 128 if offset == 0 and group is None else None,
            "findings": findings,
        },
        "presentation": {
            "source_current": False,
            "finding_labels": {"diagnostic-129": "Duplicate registry ID 42"},
        },
    }


class AxiomFindingsTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(60):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

    async def test_pages_all_findings_and_opens_exact_diagnostic_and_source(self) -> None:
        core = catalog_core()

        async def action(_session, name, *options):
            self.assertEqual(SESSION, _session)
            self.assertEqual(ATTEMPT, options[0])
            if name == "diagnostics":
                offset = int(options[options.index("--offset") + 1])
                group = (options[options.index("--group") + 1]
                         if "--group" in options else None)
                return _page(offset, group=group)
            self.assertEqual(REVISION, options[options.index("--revision") + 1])
            self.assertEqual("diagnostic-129", options[options.index("--diagnostic") + 1])
            if name == "diagnostic":
                return {"format": "workbench-developer-action-v1", "exit_code": 0,
                        "result": {"format": "workbench-material-diagnostic-evidence-v1",
                                   "diagnostic_id": REVISION, "finding": _finding(129),
                                   "native": {"message": "Duplicate registry ID 42",
                                              "severity": "ERROR"}}}
            self.assertEqual("source", name)
            return {"format": "workbench-developer-action-v1", "exit_code": 0,
                    "result": {"format": "workbench-material-source-view-v1",
                               "result_id": REVISION, "source": _finding(129),
                               "text": "\n".join(f"line {i}" for i in range(1, 80))}}

        core.developer_materials_action = AsyncMock(side_effect=action)
        app = WorkbenchApp(core)
        async with app.run_test(size=(64, 22)) as pilot:
            app.push_screen(AxiomFindingsScreen(SESSION, ATTEMPT))
            await self._settle(pilot, lambda: isinstance(app.screen, AxiomFindingsScreen)
                               and app.screen.next_offset == 128)
            screen = app.screen
            self.assertEqual(128, screen.query_one("#axiom-findings-list", OptionList).option_count)
            self.assertIn("1–128 / 130", str(screen.query_one("#axiom-findings-summary", Static).render()))
            screen.action_next_page()
            await self._settle(pilot, lambda: screen.page_offset == 128 and not screen.page_loading)
            self.assertEqual(2, screen.query_one("#axiom-findings-list", OptionList).option_count)
            await pilot.press("down", "enter")
            await self._settle(pilot, lambda: isinstance(app.screen, AxiomFindingDetailScreen)
                               and app.screen.evidence is not None)
            detail = app.screen
            self.assertEqual("diagnostic-129", detail.finding["id"])
            self.assertTrue(detail.query_one("#axiom-finding-detail", RichLog).lines)
            detail.action_source()
            await self._settle(pilot, lambda: detail.source_record is not None)
            self.assertTrue(detail.show_source)
            self.assertIn("retained source", str(detail.query_one("#axiom-finding-status", Static).render()).lower())
            await pilot.press("escape")
            await self._settle(pilot, lambda: app.screen is screen)
            screen.action_previous_page()
            await self._settle(pilot, lambda: screen.page_offset == 0 and not screen.page_loading)
            self.assertEqual(128, screen.query_one("#axiom-findings-list", OptionList).option_count)
        actions = [call.args[1] for call in core.developer_materials_action.await_args_list]
        self.assertEqual(["diagnostics", "diagnostics", "diagnostic", "source", "diagnostics"], actions)

    async def test_group_filter_reaches_located_error_without_warning_pages(self) -> None:
        core = catalog_core()

        async def action(_session, name, *options):
            self.assertEqual("diagnostics", name)
            group = options[options.index("--group") + 1] if "--group" in options else None
            return _page(int(options[options.index("--offset") + 1]), group=group)

        core.developer_materials_action = AsyncMock(side_effect=action)
        app = WorkbenchApp(core)
        async with app.run_test(size=(64, 22)) as pilot:
            app.push_screen(AxiomFindingsScreen(SESSION, ATTEMPT))
            await self._settle(pilot, lambda: isinstance(app.screen, AxiomFindingsScreen)
                               and not app.screen.page_loading and app.screen.revision == REVISION)
            screen = app.screen
            screen.action_next_group()
            await self._settle(pilot, lambda: screen.group == "error-located" and not screen.page_loading)
            self.assertEqual(1, screen.query_one("#axiom-findings-list", OptionList).option_count)
            self.assertIn("Duplicate registry ID 42",
                          str(screen.query_one("#axiom-findings-selected", Static).render()))
            self.assertFalse(screen.query_one("#axiom-findings-source", Button).disabled)
            self.assertEqual("error-located", core.developer_materials_action.await_args_list[-1].args[-1])

    async def test_unavailable_old_diagnostics_are_explained_without_losing_back(self) -> None:
        core = catalog_core()
        core.developer_materials_action = AsyncMock(side_effect=CoreClientError(
            "This older retained check has no diagnostic delivery"
        ))
        app = WorkbenchApp(core)
        async with app.run_test(size=(64, 22)) as pilot:
            app.push_screen(AxiomFindingsScreen(SESSION, ATTEMPT))
            await self._settle(pilot, lambda: isinstance(app.screen, AxiomFindingsScreen)
                               and bool(app.screen.query("#axiom-findings-status"))
                               and "older retained" in str(app.screen.query_one(
                                   "#axiom-findings-status", Static).render()))
            self.assertEqual(0, app.screen.query_one("#axiom-findings-list", OptionList).option_count)
            await pilot.press("escape")
            await self._settle(pilot, lambda: not isinstance(app.screen, AxiomFindingsScreen))


class CoreAxiomReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_diagnostic_and_source_actions_use_context_route(self) -> None:
        core = CoreClient(("workbench",))
        core.json_record = AsyncMock(return_value={
            "format": "workbench-developer-action-v1", "exit_code": 0, "result": {},
        })
        for name in ("diagnostic", "source"):
            await core.developer_materials_action(
                SESSION, name, ATTEMPT, "--revision", REVISION,
                "--diagnostic", "diagnostic-129",
            )
        calls = [call.args[:6] for call in core.json_record.await_args_list]
        self.assertEqual([
            ("context", "run", SESSION, "--", "checks", "materials"),
            ("context", "run", SESSION, "--", "checks", "materials"),
        ], calls)
        self.assertEqual(["diagnostic", "source"], [
            call.args[6] for call in core.json_record.await_args_list
        ])


if __name__ == "__main__":
    unittest.main()
