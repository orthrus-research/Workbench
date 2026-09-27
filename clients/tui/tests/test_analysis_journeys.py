"""Keyboard journeys and truthful result presentation for Atlas and Axiom."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from textual.widgets import Button, Input, OptionList, RichLog

from test_workflows_screen import catalog_core, DIGEST
from workbench_tui.app import (
    AnalysisResultScreen, AtlasImportScreen, AtlasObservationSearchScreen,
    AtlasSearchScreen, AxiomHistoryScreen, AxiomJourneyScreen, WorkspaceChoicesScreen,
    EnvironmentView, ReviewModal, SetupScreen, WorkbenchApp, _analysis_summary,
    _atlas_import_action, _atlas_match_name, _atlas_record, _axiom_import_source,
    _observation_label,
)
from workbench_tui.core_client import CommandOutput, CoreClient, CoreClientError


SESSION = "work-session-v2-" + "a" * 32


def owner_output(command_id: str, record: dict) -> CommandOutput:
    lines = json.dumps(record, indent=2).splitlines()
    events = [
        {"source": "workbench", "stream": None, "message": "stage started"},
        *({"source": command_id, "stream": "stdout", "message": line}
          for line in lines),
        {"source": "workbench", "stream": None, "message": "stage completed"},
    ]
    return CommandOutput((), 0, "\n".join(json.dumps(row) for row in events), "")


class AnalysisResultTests(unittest.TestCase):
    def test_source_inspection_shows_location_without_opaque_selection_id(self) -> None:
        opaque = "source-text:" + "a" * 64 + ":groovy%2Fclasses%2FCoolant.groovy:245:252"
        summary = _analysis_summary("atlas", {
            "format": "workbench-atlas-recipe-health-report-v1",
            "context": {"context_type": "source-only-checkout", "root": "/saved-pack"},
            "selection": {"kind": "source-occurrence", "selection_id": opaque,
                          "source_path": "groovy/classes/Coolant.groovy",
                          "line": 10, "column": 16,
                          "snippet": "public int circuit = 0;"},
        })
        self.assertIn("Selection: groovy/classes/Coolant.groovy:10:16", summary)
        self.assertIn("Source text: public int circuit = 0;", summary)
        self.assertNotIn(opaque, summary)

    def test_observation_uses_record_key_without_dumping_raw_value(self) -> None:
        node = {"id": "workbench-atlas-node-v2:example:" + "a" * 64,
                "semantic_key": "a" * 64, "kind": "initialization-block-hardness",
                "properties": {"record_key": "tardis:circuit_repair",
                               "family": "block-hardness",
                               "label": "net.tardis.mod.common.blocks.BlockComponentRepair",
                               "raw_value": {"long": "internal value"}}}
        self.assertEqual("tardis:circuit_repair", _observation_label(node))
        self.assertEqual("tardis:circuit_repair", _observation_label(node, limit=30))
        summary = _analysis_summary("atlas", {
            "format": "workbench-atlas-observation-inspection-v1",
            "context": {"root": "/graph"}, "selection": node,
            "relationships": {"incoming": {"uses": 2}, "outgoing": {"contains": 1}},
        })
        self.assertIn("Observation: tardis:circuit_repair", summary)
        self.assertIn("Incoming links: 2", summary)
        self.assertNotIn("internal value", summary)
        self.assertNotIn("a" * 64, summary)
        self.assertIn("R Full record", summary)

    def test_axiom_to_atlas_uses_owner_attempt_and_admitted_catalog_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            attempt = Path(temporary) / "retained-check"
            attempt.mkdir()
            record = {"result": {"snapshot_id": "snapshot-id"},
                      "presentation": {"attempt_uri": attempt.as_uri()}}
            self.assertEqual(attempt, _axiom_import_source(record))
            self.assertIsNone(_axiom_import_source({
                **record, "presentation": {"attempt_uri": "https://example.test/retained-check"}}))
            self.assertIsNone(_axiom_import_source({
                **record, "result": {"attempt_id": "only-an-id"}}))
        action = {"command_id": "atlas.observations-import-snapshot",
                  "availability": "experimental", "risk": "mutating", "preview": "inert-only"}
        self.assertEqual(action, _atlas_import_action({"commands": [action]}))
        self.assertIsNone(_atlas_import_action({"commands": [
            {**action, "availability": "unavailable"}]}))
        self.assertIsNone(_atlas_import_action({"commands": [
            {**action, "risk": "read-only"}]}))

    def test_jsonl_owner_output_is_reassembled_without_core_stage_lines(self) -> None:
        record = {"format": "workbench-atlas-recipe-health-search-v1", "query": "water",
                  "results": []}
        self.assertEqual(record, _atlas_record(owner_output("atlas.recipes-search", record),
                                               "atlas.recipes-search"))
        with self.assertRaisesRegex(CoreClientError, "readable result"):
            _atlas_record(owner_output("another.action", record), "atlas.recipes-search")

    def test_source_only_result_and_axiom_page_show_their_real_scope(self) -> None:
        self.assertEqual("groovy/test.groovy:7:4", _atlas_match_name({
            "kind": "source-occurrence", "source_path": "groovy/test.groovy",
            "line": 7, "column": 4, "snippet": "MIXER.recipeBuilder()",
        }))
        atlas = _analysis_summary("atlas", {
            "format": "workbench-atlas-recipe-health-search-v1",
            "context": {"context_type": "source-only-checkout", "root": "/saved-pack"},
            "query": "water", "results": [],
            "evidence_gaps": [{"code": "runtime-recipes-unavailable"}],
        })
        self.assertIn("text occurrences, not observed recipes", atlas)
        self.assertIn("runtime-recipes-unavailable", atlas)
        axiom = _analysis_summary("axiom", {
            "format": "workbench-developer-action-v1", "exit_code": 1,
            "result": {"state": "completed", "native_status": "rejected",
                       "attempt_id": "attempt-1", "findings_count": 1,
                       "finding_page": {"payload": {"records": [
                           {"value": {"id": "f-1", "message": "Original native failure"}},
                       ]}}},
            "presentation": {"finding_labels": {"f-1": "Original native failure"}},
        })
        self.assertIn("Native status: rejected", axiom)
        self.assertIn("Findings in this page: 1", axiom)
        self.assertIn("Original native failure", axiom)
        mixed_coverage = _analysis_summary("axiom", {
            "result": {"state": "completed", "coverage": "complete",
                       "native": {"result": {"assessment": {"coverage": "incomplete"}}}},
        })
        self.assertIn("Captured evidence: complete", mixed_coverage)
        self.assertIn("Native assessment: incomplete", mixed_coverage)
        self.assertNotIn("\nCoverage: complete", mixed_coverage)
        bounded = _analysis_summary("axiom", {
            "result": {"state": "completed", "findings_count": 101,
                       "finding_page": {"payload": {"records": [
                           {"value": {"id": f"finding-{index}",
                                      "message": f"Finding number {index}"}}
                           for index in range(101)
                       ]}}},
        })
        self.assertIn("Findings in this page: 101 · showing 3", bounded)
        self.assertIn("Finding number 2", bounded)
        self.assertNotIn("Finding number 3", bounded)
        warnings = [{"value": {"id": f"warning-{index}", "severity": "warning",
                               "message": f"Warning {index}"}} for index in range(141)]
        errors = [{"value": {"id": f"error-{index}", "severity": "ERROR",
                             "message": "generic"}} for index in range(2)]
        prioritized = _analysis_summary("axiom", {
            "result": {"state": "completed", "findings_count": 406,
                       "detail_state": "not-loaded",
                       "finding_page": {"payload": {"records": warnings + errors}}},
            "presentation": {"finding_labels": {
                "error-0": "Original native failure",
                "error-1": "Registration stopped",
            }},
        })
        self.assertIn("Page severity: 2 error, 141 warning", prioritized)
        self.assertLess(prioritized.index("Original native failure"),
                        prioritized.index("Warning 0"))
        self.assertIn("Findings in this page: 143 · showing 8", prioritized)
        self.assertIn("263 further findings are retained beyond this page", prioritized)
        self.assertNotIn("Warning 6", prioritized)


class CoreAnalysisContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_atlas_import_requires_mutating_review_and_exact_binding(self) -> None:
        core = CoreClient(("workbench",))
        core.call = AsyncMock(return_value=CommandOutput((), 0, "", ""))
        catalog = {"catalog_digest": DIGEST}
        action = {"command_id": "atlas.observations-import-snapshot",
                  "action_digest": DIGEST, "risk": "mutating", "preview": "inert-only",
                  "options": [{"key": key} for key in
                              ("path", "pack_profile", "output", "side", "json")]}
        values = {"path": "/retained/attempt", "pack_profile": "supersymmetry",
                  "output": "/new/graph", "side": "single", "json": True}
        with self.assertRaisesRegex(CoreClientError, "review"):
            await core.import_reviewed_atlas_snapshot(
                catalog, action, values, {"risk": "read-only", "review_digest": DIGEST})
        core.call.assert_not_awaited()
        await core.import_reviewed_atlas_snapshot(
            catalog, action, values, {"risk": "mutating", "review_digest": DIGEST})
        args = core.call.await_args.args
        self.assertIn("--expect-review-digest", args)
        self.assertIn("--execute", args)
        self.assertIn("jsonl", args)
        self.assertIn('path:="/retained/attempt"', args)
        self.assertIn('output:="/new/graph"', args)

    async def test_axiom_context_route_keeps_native_outcome_records(self) -> None:
        core = CoreClient(("workbench",))
        core.json_record = AsyncMock(side_effect=[
            {"session_id": SESSION},
            {"format": "workbench-developer-action-v1", "exit_code": 1,
             "result": {"state": "completed", "native_status": "rejected"}},
        ])
        selected = await core.developer_context_select("/saved-pack")
        self.assertEqual(SESSION, selected["session_id"])
        result = await core.developer_materials_action(
            SESSION, "run", "--context", "supersymmetry:material-authoring-pack"
        )
        self.assertEqual("rejected", result["result"]["native_status"])
        call = core.json_record.await_args
        self.assertEqual(call.args[:7],
                         ("context", "run", SESSION, "--", "checks", "materials", "run"))
        self.assertEqual((0, 1, 2, 3, 4), call.kwargs["allowed_exit"])
        self.assertEqual(3600, call.kwargs["timeout"])

    async def test_axiom_owner_unavailable_reason_is_shown(self) -> None:
        core = CoreClient(("workbench",))
        core.json_record = AsyncMock(return_value={
            "state": "unavailable",
            "reason": "Core setup has no selected Java; choose one in Workbench.",
        })
        with self.assertRaisesRegex(CoreClientError, "no selected Java"):
            await core.developer_materials_action(SESSION, "setup", "--prepare")

    async def test_installed_engine_lookup_accepts_verified_or_unavailable(self) -> None:
        core = CoreClient(("workbench",))
        core.json_record = AsyncMock(return_value={
            "format": "workbench-installed-axiom-engine-v1",
            "state": "unavailable", "archive_path": None, "reason": "No bundle installed",
        })
        self.assertEqual("unavailable", (await core.installed_axiom_engine())["state"])
        self.assertEqual((0, 1), core.json_record.await_args.kwargs["allowed_exit"])
        self.assertEqual(("installed", "axiom-engine", "--json"),
                         core.json_record.await_args.args)
        core.json_record.return_value = {
            "format": "workbench-installed-axiom-engine-v1",
            "state": "verified", "archive_path": "/saved/bundle/axiom/engine.zip",
        }
        self.assertEqual("/saved/bundle/axiom/engine.zip",
                         (await core.installed_axiom_engine())["archive_path"])


class AnalysisJourneyTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(60):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

    async def test_source_only_atlas_search_opens_exact_selection_by_enter(self) -> None:
        core = catalog_core()
        inspect = {
            "command_id": "atlas.recipes-inspect", "title": "Inspect recipe selection",
            "summary": "Inspect exact evidence", "suite_id": "atlas", "authority": "Atlas",
            "risk": "read-only", "preview": "none", "availability": "experimental",
            "action_digest": DIGEST,
            "options": [{"key": key, "kind": kind, "nargs": "one", "repeat": False,
                         "required_group": False} for key, kind in
                        (("path", "path"), ("selection_id", "text"), ("json", "boolean"))],
            "document": None,
        }
        catalog = {"catalog_digest": DIGEST, "commands": [inspect]}
        search = {
            "format": "workbench-atlas-recipe-health-search-v1",
            "context": {"context_type": "source-only-checkout", "root": "/saved-pack"},
            "query": "water", "results": [{
                "selection_id": "source-text:one", "kind": "source-occurrence",
                "source_path": "groovy/test.groovy", "line": 7, "column": 4,
                "snippet": "fluid('water')",
            }],
        }
        core.run_reviewed_command = AsyncMock(return_value=owner_output(
            "atlas.recipes-inspect", {
                "format": "workbench-atlas-recipe-health-report-v1",
                "context": search["context"], "role": "source-occurrence",
                "selection": search["results"][0],
            }
        ))
        app = WorkbenchApp(core)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.push_screen(AtlasSearchScreen(catalog, "/saved-pack", search))
            await self._settle(pilot, lambda: isinstance(app.screen, AtlasSearchScreen)
                               and bool(app.screen.query("#atlas-search-results"))
                               and app.screen.query_one("#atlas-search-results", OptionList).option_count == 1)
            screen = app.screen
            self.assertIn("groovy/test.groovy:7:4",
                          str(screen.query_one("#atlas-search-results", OptionList).options[0].prompt))
            self.assertTrue(screen.query_one("#atlas-search-browse", Button).disabled)
            self.assertFalse(screen.query_one("#atlas-search-browse", Button).display)
            self.assertNotIn("B Browse links", str(screen.query_one(".keyboard-hint").content))
            await pilot.press("b")
            self.assertIs(app.screen, screen)
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
            self.assertEqual("source-text:one",
                             core.command_review.await_args.args[2]["selection_id"])
            self.assertEqual("jsonl", core.run_reviewed_command.await_args.kwargs["console"])

    async def test_axiom_run_waits_for_ready_setup_and_explicit_confirmation(self) -> None:
        core = catalog_core()
        core.developer_context_select = AsyncMock(return_value={"session_id": SESSION})

        async def action(_session, name, *_options):
            if name == "setup-status":
                return {"format": "workbench-developer-action-v1", "exit_code": 0,
                        "result": {"state": "ready"}}
            if name == "show":
                self.assertEqual("attempt-1", _options[0])
                return {"format": "workbench-developer-action-v1", "exit_code": 1,
                        "result": {"state": "completed", "native_status": "rejected",
                                   "attempt_id": "attempt-1", "findings_count": 1,
                                   "finding_page": {"payload": {"records": [
                                       {"value": {"id": "finding-1", "message": "generic"}},
                                   ]}}},
                        "presentation": {"finding_labels": {
                            "finding-1": "Material registration failed in native run",
                        }}}
            return {"format": "workbench-developer-action-v1", "exit_code": 1,
                    "result": {"state": "completed", "native_status": "rejected",
                               "attempt_id": "attempt-1", "findings_count": 1}}

        core.developer_materials_action = AsyncMock(side_effect=action)
        app = WorkbenchApp(core)
        with tempfile.TemporaryDirectory() as temporary:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.push_screen(AxiomJourneyScreen(EnvironmentView(
                    environment={"workspace": {"path": temporary}})))
                await self._settle(pilot, lambda: isinstance(app.screen, AxiomJourneyScreen)
                                   and bool(app.screen.query("#axiom-run")))
                screen = app.screen
                self.assertTrue(screen.query_one("#axiom-run", Button).disabled)
                screen.check_setup()
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                await pilot.press("escape")
                await self._settle(pilot, lambda: app.screen is screen)
                self.assertFalse(screen.query_one("#axiom-run", Button).disabled)
                screen.run_check()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                self.assertEqual(0, sum(call.args[1] == "run" for call
                                        in core.developer_materials_action.await_args_list))
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                self.assertIn("Native status: rejected",
                              _analysis_summary("axiom", app.screen.record))
                self.assertIn("Material registration failed in native run",
                              _analysis_summary("axiom", app.screen.record))
                self.assertEqual(["setup-status", "run", "show"], [
                    call.args[1] for call in core.developer_materials_action.await_args_list[-3:]
                ])

    async def test_axiom_run_keeps_run_record_if_saved_page_cannot_reopen(self) -> None:
        core = catalog_core()
        core.developer_context_select = AsyncMock(return_value={"session_id": SESSION})

        async def action(_session, name, *_options):
            if name == "setup-status":
                return {"format": "workbench-developer-action-v1", "exit_code": 0,
                        "result": {"state": "ready"}}
            if name == "show":
                raise CoreClientError("saved detail unavailable")
            return {"format": "workbench-developer-action-v1", "exit_code": 1,
                    "result": {"state": "completed", "native_status": "native-failed",
                               "attempt_id": "attempt-1", "findings_count": 1}}

        core.developer_materials_action = AsyncMock(side_effect=action)
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(core)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.push_screen(AxiomJourneyScreen(EnvironmentView(
                    environment={"workspace": {"path": temporary}})))
                await self._settle(pilot, lambda: isinstance(app.screen, AxiomJourneyScreen)
                                   and bool(app.screen.query("#axiom-run")))
                screen = app.screen
                screen.check_setup()
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                await pilot.press("escape")
                await self._settle(pilot, lambda: app.screen is screen and not screen.busy)
                screen.run_check()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                self.assertEqual("attempt-1", app.screen.record["result"]["attempt_id"])
                self.assertIn("Native status: native-failed",
                              _analysis_summary("axiom", app.screen.record))

    async def test_axiom_keyboard_can_open_java_setup(self) -> None:
        core = catalog_core()
        core.java_inventory = AsyncMock(return_value={"candidates": []})
        core.workspace_choices = AsyncMock(return_value={"entries": [], "default": None})
        app = WorkbenchApp(core)
        async with app.run_test(size=(58, 24)) as pilot:
            await pilot.pause()
            app.push_screen(AxiomJourneyScreen(EnvironmentView()))
            await self._settle(pilot, lambda: isinstance(app.screen, AxiomJourneyScreen)
                               and bool(app.screen.query("#axiom-pack.keyboard-selected")))
            for _ in range(7):
                await pilot.press("down")
            screen = app.screen
            self.assertTrue(screen.query_one("#axiom-java", Button).has_class("keyboard-selected"))
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen))

    async def test_axiom_prepare_passes_selected_java_executable_to_owner(self) -> None:
        core = catalog_core()
        core.developer_context_select = AsyncMock(return_value={"session_id": SESSION})
        core.developer_materials_action = AsyncMock(return_value={
            "format": "workbench-developer-action-v1", "exit_code": 0,
            "result": {"state": "configured-not-run"},
        })
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"
            pack.mkdir()
            engine = root / "engine.zip"
            java = root / "jdk" / "bin" / "java"
            engine.write_bytes(b"placeholder")
            java.parent.mkdir(parents=True)
            java.write_bytes(b"placeholder")
            app = WorkbenchApp(core)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.push_screen(AxiomJourneyScreen(EnvironmentView(
                    environment={"workspace": {"path": str(pack)}})))
                await self._settle(pilot, lambda: isinstance(app.screen, AxiomJourneyScreen)
                                   and bool(app.screen.query("#axiom-engine")))
                screen = app.screen
                screen.query_one("#axiom-engine", Input).value = str(engine)
                screen.query_one("#axiom-java-path", Input).value = str(java)
                await self._settle(pilot, lambda: not screen.query_one(
                    "#axiom-prepare", Button).disabled)
                screen.query_one("#axiom-prepare", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                core.developer_materials_action.assert_not_awaited()
                self.assertIn(str(java), app.screen.body)
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                args = core.developer_materials_action.await_args.args
                self.assertEqual(SESSION, args[0])
                self.assertEqual("setup", args[1])
                self.assertIn("--engine-archive", args)
                self.assertIn(str(engine), args)
                self.assertIn("--java", args)
                self.assertIn(str(java), args)

    async def test_axiom_engine_prefills_only_from_verified_core_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "verified-engine.zip"
            archive.write_bytes(b"placeholder")
            for state in ("verified", "unavailable"):
                with self.subTest(state=state):
                    core = catalog_core()
                    core.installed_axiom_engine = AsyncMock(return_value={
                        "format": "workbench-installed-axiom-engine-v1",
                        "state": state,
                        "archive_path": str(archive) if state == "verified" else None,
                        "reason": None if state == "verified" else "No installed bundle",
                    })
                    app = WorkbenchApp(core)
                    async with app.run_test(size=(58, 24)) as pilot:
                        await pilot.pause()
                        app.push_screen(AxiomJourneyScreen(EnvironmentView()))
                        await self._settle(pilot, lambda: isinstance(app.screen, AxiomJourneyScreen)
                                           and core.installed_axiom_engine.await_count == 1)
                        await pilot.pause(0.05)
                        screen = app.screen
                        self.assertEqual(str(archive) if state == "verified" else "",
                                         screen.query_one("#axiom-engine", Input).value)
                        self.assertEqual(state == "verified",
                                         not screen.query_one("#axiom-prepare", Button).disabled)
                        self.assertFalse(screen.query_one("#axiom-engine-browse", Button).disabled)

    async def test_axiom_history_explains_expired_check_and_opens_retained_check(self) -> None:
        core = catalog_core()
        core.developer_materials_action = AsyncMock(return_value={
            "format": "workbench-developer-action-v1", "exit_code": 0,
            "result": {"state": "completed", "attempt_id": "retained-check"},
        })
        history = {"result": {"attempts": [
            {"attempt_id": "expired-check", "state": "expired",
             "original_summary": "The original retained files have expired."},
            {"attempt_id": "retained-check", "state": "completed",
             "record_id": "record-1"},
        ]}}
        app = WorkbenchApp(core)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.push_screen(AxiomHistoryScreen(SESSION, history))
            await self._settle(pilot, lambda: isinstance(app.screen, AxiomHistoryScreen)
                               and app.screen.selected == "expired-check")
            screen = app.screen
            self.assertTrue(screen.query_one("#axiom-history-open", Button).disabled)
            self.assertIn("expired", str(screen.query_one("#axiom-history-status").content))
            await pilot.press("enter")
            core.developer_materials_action.assert_not_awaited()
            await pilot.press("down")
            await self._settle(pilot, lambda: screen.selected == "retained-check")
            self.assertFalse(screen.query_one("#axiom-history-open", Button).disabled)
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
            self.assertEqual((SESSION, "show", "retained-check"),
                             core.developer_materials_action.await_args.args)

    async def test_axiom_to_atlas_import_needs_review_and_uses_exact_attempt(self) -> None:
        core = catalog_core()
        action = {
            "command_id": "atlas.observations-import-snapshot", "title": "Import Axiom snapshot",
            "suite_id": "atlas", "authority": "Atlas", "risk": "mutating",
            "preview": "inert-only", "availability": "experimental", "action_digest": DIGEST,
            "options": [{"key": key} for key in
                        ("path", "pack_profile", "output", "side", "json")],
            "document": None,
        }
        core.catalog.return_value["commands"].append(action)
        core.catalog.return_value["commands"].append({
            "command_id": "atlas.observations-search", "title": "Search observations",
            "risk": "read-only", "preview": "none", "availability": "experimental",
            "options": [{"key": key, "kind": "text", "nargs": "one"}
                        for key in ("path", "query", "limit", "json")],
            "document": None,
        })
        core.command_review = AsyncMock(return_value={"risk": "mutating",
                                                      "review_digest": DIGEST})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / "retained-attempt"
            evidence = root / "evidence"
            attempt.mkdir()
            evidence.mkdir()
            expected_output = evidence / "atlas-retained-attempt"
            core.environment_resolve = AsyncMock(return_value={
                "format": "workbench-environment-resolution-v1",
                "resolution_id": "workbench-environment-resolution:sha256:" + "a" * 64,
                "workspace": {"path": str(root), "source": "user-workspaces"},
                "locations": {"evidence": {"path": str(evidence)}},
            })
            core.workspace_home = AsyncMock(return_value={
                "format": "workbench-workspace-home-v2", "workspace": {}, "status": {},
            })
            core.import_reviewed_atlas_snapshot = AsyncMock(return_value=owner_output(
                "atlas.observations-import-snapshot", {
                    "format": "workbench-atlas-initialization-projection-v1",
                    "state": "complete", "root": str(expected_output),
                }
            ))
            retained = {
                "format": "workbench-developer-action-v1", "exit_code": 1,
                "result": {"snapshot_id": "snapshot-id", "attempt_id": "retained-attempt",
                           "state": "completed", "native_status": "native-failed"},
                "presentation": {"attempt_uri": attempt.as_uri()},
            }
            app = WorkbenchApp(core)
            async with app.run_test(size=(100, 30)) as pilot:
                await self._settle(pilot, lambda: app.view.catalog is not None)
                app.push_screen(AnalysisResultScreen("Axiom native check", "axiom", retained))
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen)
                                   and bool(app.screen.query("#analysis-import-atlas")))
                self.assertIsInstance(app.focused, RichLog)
                self.assertIn("I Open in Atlas", str(app.screen.query_one(".keyboard-hint").content))
                await pilot.press("i")
                await self._settle(pilot, lambda: isinstance(app.screen, AtlasImportScreen))
                importer = app.screen
                self.assertEqual(str(expected_output), importer.query_one("#atlas-import-output").value)
                importer.query_one("#atlas-import-run", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                core.import_reviewed_atlas_snapshot.assert_not_awaited()
                await pilot.click("#review-cancel")
                await self._settle(pilot, lambda: app.screen is importer)
                core.import_reviewed_atlas_snapshot.assert_not_awaited()
                importer.query_one("#atlas-import-run", Button).press()
                await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
                await pilot.click("#review-confirm")
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                values = core.import_reviewed_atlas_snapshot.await_args.args[2]
                self.assertEqual(str(attempt), values["path"])
                self.assertEqual(str(expected_output), values["output"])
                self.assertEqual("single", values["side"])
                self.assertTrue(values["json"])
                session = Mock()
                session.request = AsyncMock(return_value={
                    "format": "workbench-atlas-observation-search-v1",
                    "context": {"root": str(expected_output)}, "query": "iron",
                    "results": [], "page": {"next_cursor": None},
                })
                session.close = AsyncMock()
                core.open_atlas_observation_session = AsyncMock(return_value=session)
                await pilot.press("s")
                await self._settle(pilot, lambda: isinstance(app.screen, AtlasObservationSearchScreen)
                                   and app.screen.session is session)
                search = app.screen
                search.query_one("#atlas-observation-query", Input).value = "iron"
                search.query_one("#atlas-observation-search", Button).press()
                await self._settle(pilot, lambda: session.request.await_count == 1)
                session.request.assert_awaited_once_with("search", {"query": "iron", "limit": 50})
                core.run_reviewed_command.assert_not_awaited()

    async def test_import_receipt_opens_graph_search_and_keyboard_inspection(self) -> None:
        core = catalog_core()
        catalog = core.catalog.return_value
        for command_id in ("atlas.observations-search", "atlas.observations-inspect"):
            catalog["commands"].append({
                "command_id": command_id,
                "title": command_id.rsplit("-", 1)[-1].capitalize(),
                "suite_id": "atlas", "authority": "Atlas", "risk": "read-only",
                "preview": "none", "availability": "experimental", "action_digest": DIGEST,
                "options": [{"key": key, "kind": "text", "nargs": "one"}
                            for key in ("path", "query", "selection_id", "limit", "json")],
                "document": None,
            })
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "atlas-observations"
            root.mkdir()
            first_node = {"id": "observation-1", "kind": "material",
                          "semantic_key": "workbench:iron", "evidence": []}
            second_node = {"id": "observation-2", "kind": "material",
                           "semantic_key": "workbench:copper", "evidence": []}

            async def session_result(operation, arguments):
                if operation == "search":
                    self.assertEqual("iron", arguments["query"])
                    return {
                        "format": "workbench-atlas-observation-search-v1",
                        "query": "iron", "context": {"root": str(root)},
                        "results": [first_node, second_node], "page": {"next_cursor": None},
                    }
                self.assertEqual("inspect", operation)
                self.assertEqual("observation-2", arguments["selection_id"])
                return {
                    "format": "workbench-atlas-observation-inspection-v1",
                    "context": {"root": str(root)}, "selection": second_node,
                }

            session = Mock()
            session.request = AsyncMock(side_effect=session_result)
            session.close = AsyncMock()
            core.open_atlas_observation_session = AsyncMock(return_value=session)
            app = WorkbenchApp(core)
            async with app.run_test(size=(100, 30)) as pilot:
                await self._settle(pilot, lambda: app.view.catalog is not None)
                app.push_screen(AnalysisResultScreen("Atlas import", "atlas", {
                    "format": "workbench-atlas-initialization-projection-v1",
                    "state": "complete", "root": str(root),
                }))
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen)
                                   and bool(app.screen.query("#analysis-search-graph")))
                self.assertIsInstance(app.focused, RichLog)
                self.assertIn("S Search graph", str(app.screen.query_one(".keyboard-hint").content))
                await pilot.press("s")
                await self._settle(pilot, lambda: isinstance(app.screen, AtlasObservationSearchScreen)
                                   and app.screen.session is session)
                search = app.screen
                await pilot.press("enter", "s", "i", "l", "m")
                self.assertEqual("silm", search.query_one("#atlas-observation-query", Input).value)
                session.request.assert_not_awaited()
                await pilot.press("escape")
                self.assertEqual("", search.query_one("#atlas-observation-query", Input).value)
                search.query_one("#atlas-observation-query", Input).value = "iron"
                await pilot.press("s")
                await self._settle(pilot, lambda: search.selected is not None and not search.busy)
                self.assertEqual("observation-1", search.selected["id"])
                self.assertFalse(search.query_one("#atlas-observation-more", Button).display)
                self.assertIs(app.screen, search)
                self.assertIsInstance(app.focused, OptionList)
                self.assertEqual(0, app.focused.highlighted)
                await pilot.press("down")
                await self._settle(pilot, lambda: search.selected is not None
                                   and search.selected["id"] == "observation-2")
                self.assertEqual(1, app.focused.highlighted)
                await pilot.press("right")
                self.assertTrue(search.query_one("#atlas-observation-links", Button)
                                .has_class("keyboard-selected"))
                await pilot.press("up", "up", "enter")
                await self._settle(pilot, lambda: isinstance(app.focused, OptionList)
                                   and not search.busy)
                self.assertIs(app.screen, search)
                await pilot.press("left")
                self.assertIs(app.screen, search)
                self.assertTrue(search.query_one("#atlas-observation-query", Input)
                                .has_class("keyboard-selected"))
                await pilot.press("down", "enter")
                await self._settle(pilot, lambda: isinstance(app.focused, OptionList)
                                   and not search.busy)
                self.assertEqual("observation-1", search.selected["id"])
                await pilot.press("down")
                await self._settle(pilot, lambda: search.selected is not None
                                   and search.selected["id"] == "observation-2")
                await pilot.press("enter")
                await pilot.pause(0.1)
                self.assertEqual(4, session.request.await_count,
                                 str(search.query_one("#atlas-observation-status").content)
                                 + " focus=" + repr(app.focused)
                                 + " selected=" + repr(search.selected))
                await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen))
                self.assertIn("Observation: workbench:copper",
                              _analysis_summary("atlas", app.screen.record))
                self.assertEqual(4, session.request.await_count)
                await pilot.press("escape")
                await self._settle(pilot, lambda: app.screen is search)
                session.close.assert_not_awaited()
                await pilot.press("escape")
                await self._settle(pilot, lambda: app.screen is not search)
                session.close.assert_awaited_once()

    async def test_result_shortcuts_do_not_open_unavailable_actions(self) -> None:
        core = catalog_core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(58, 24)) as pilot:
            await self._settle(pilot, lambda: app.view.catalog is not None)
            app.push_screen(AnalysisResultScreen("Result", "axiom", {
                "format": "workbench-developer-action-v1", "exit_code": 1,
                "result": {"state": "completed", "snapshot_id": "snapshot-1"},
            }))
            await self._settle(pilot, lambda: isinstance(app.screen, AnalysisResultScreen)
                               and bool(app.screen.query("#result-log"))
                               and isinstance(app.focused, RichLog))
            screen = app.screen
            self.assertFalse(screen.query("#analysis-import-atlas"))
            self.assertFalse(screen.query("#analysis-search-graph"))
            hint = str(screen.query_one(".keyboard-hint").content)
            self.assertNotIn("I Open in Atlas", hint)
            self.assertNotIn("S Search graph", hint)
            await pilot.press("i", "s")
            self.assertIs(app.screen, screen)

    async def test_leaving_graph_closes_session_during_pending_search(self) -> None:
        core = catalog_core()
        core.catalog.return_value["commands"].append({
            "command_id": "atlas.observations-search", "title": "Search observations",
            "risk": "read-only", "preview": "none", "availability": "experimental",
            "options": [{"key": "path", "kind": "path", "nargs": "one"}],
            "document": None,
        })
        entered = asyncio.Event()
        hold = asyncio.Event()

        async def pending(_operation, _arguments):
            entered.set()
            await hold.wait()
            return {"format": "workbench-atlas-observation-search-v1",
                    "results": [], "page": {"next_cursor": None}}

        session = Mock()
        session.request = AsyncMock(side_effect=pending)
        session.close = AsyncMock()
        core.open_atlas_observation_session = AsyncMock(return_value=session)
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(core)
            async with app.run_test(size=(58, 24)) as pilot:
                await self._settle(pilot, lambda: app.view.catalog is not None)
                screen = AtlasObservationSearchScreen(app.view.catalog, Path(temporary))
                app.push_screen(screen)
                await self._settle(pilot, lambda: screen.session is session)
                screen.query_one("#atlas-observation-query", Input).value = "circuit"
                screen.query_one("#atlas-observation-search", Button).press()
                await asyncio.wait_for(entered.wait(), timeout=2)
                app.pop_screen()
                await self._settle(pilot, lambda: app.screen is not screen)
                await self._settle(pilot, lambda: session.close.await_count == 1)
                session.close.assert_awaited_once()
                hold.set()

    async def test_leaving_graph_cancels_pending_verification(self) -> None:
        core = catalog_core()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def verify(_path):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        core.open_atlas_observation_session = AsyncMock(side_effect=verify)
        with tempfile.TemporaryDirectory() as temporary:
            app = WorkbenchApp(core)
            async with app.run_test(size=(58, 24)) as pilot:
                await self._settle(pilot, lambda: app.view.catalog is not None)
                screen = AtlasObservationSearchScreen(app.view.catalog, Path(temporary))
                app.push_screen(screen)
                await asyncio.wait_for(started.wait(), timeout=2)
                self.assertTrue(screen.query_one("#atlas-observation-search", Button).disabled)
                self.assertIn("Verifying graph once", str(screen.query_one(
                    "#atlas-observation-status").content))
                app.pop_screen()
                await asyncio.wait_for(cancelled.wait(), timeout=2)


if __name__ == "__main__":
    unittest.main()
