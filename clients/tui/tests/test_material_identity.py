"""Source-bound material identity review in the compact Textual client."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from textual.widgets import Button, Input, OptionList, RichLog

from workbench_tui.app import WorkbenchApp
from workbench_tui.core_client import CoreClientError
from workbench_tui.material_identity import (
    MaterialIdentityDetailScreen, MaterialIdentityScreen, _identity_rows,
    _row_details,
)


REPORT = {
    "format": "workbench-groovy-pack-program-report-v1",
    "candidate": {
        "collisions": [{
            "identity_kind": "gtceu-material-id", "value": 32000,
            "execution_state": "enabled", "occurrences": [
                {"source": {"path": "material/A.groovy", "line": 7, "column": 17},
                 "identity": {"numeric_id": 32000, "registry_name": "alpha"}},
                {"source": {"path": "material/B.groovy", "line": 10, "column": 16},
                 "identity": {"numeric_id": 32000, "registry_name": "beta"}},
            ],
        }],
        "effects": [
            {"rule_id": "gtceu-material-definition", "fields": {
                "numeric_id": 32000, "registry_name": "alpha"},
             "field_states": {"numeric_id": "literal", "registry_name": "literal"},
             "source": {"path": "material/A.groovy", "line": 7, "column": 17},
             "expression": "new Material.Builder(32000, alpha)",
             "lifecycle": {"stage": "preInit"}},
            {"rule_id": "gtceu-material-definition", "fields": {"numeric_id": 32001},
             "field_states": {"numeric_id": "literal", "registry_name": "unresolved"},
             "source": {"path": "material/Dynamic.groovy", "line": 4, "column": 3},
             "expression": "new Material.Builder(32001, name)",
             "lifecycle": {"stage": "preInit"}},
        ],
    },
}


class _Core:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    async def version(self):
        raise CoreClientError("test Core not connected")

    async def json_record(self, *args: str, **kwargs):
        self.calls.append((args, kwargs))
        return REPORT


class MaterialRowsTests(unittest.TestCase):
    def test_duplicate_unresolved_and_declaration_retain_static_scope(self) -> None:
        rows, counts = _identity_rows(REPORT)
        self.assertEqual({"duplicate": 1, "unresolved": 1, "declaration": 1}, counts)
        self.assertEqual("DUPLICATE MATERIAL ID 32000 · 2 sites", rows[0].label)
        self.assertIn("material/A.groovy:7:17", _row_details(rows[0], preview=False))
        self.assertIn("material/B.groovy:10:16", _row_details(rows[0], preview=False))
        self.assertIn("Runtime registry has not been checked", _row_details(rows[0], preview=False))
        self.assertIn("+1 more source sites", _row_details(rows[0], preview=True))
        self.assertIn("UNRESOLVED MATERIAL registry name", rows[2].label)
        self.assertIn("SOURCE MATERIAL 32000", rows[1].label)

    def test_unknown_report_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            _identity_rows({"format": "wrong", "candidate": {}})

    def test_compact_core_report_keeps_all_profile_collision_kinds(self) -> None:
        compact = {
            "format": "workbench-groovy-identity-summary-v1",
            "collisions": [
                {"identity_kind": "crafting-recipe-id", "value": "example:gear",
                 "occurrences": [
                     {"source": {"path": "postInit/Crafting.groovy", "line": 2,
                                 "column": 1}, "identity": {"recipe_id": "example:gear"}},
                     {"source": {"path": "postInit/Crafting.groovy", "line": 9,
                                 "column": 1}, "identity": {"recipe_id": "example:gear"}},
                 ]},
            ],
            "identity_declarations": [{
                "rule_id": "groovyscript-crafting-registration",
                "fields": {"recipe_id": "example:gear"},
                "field_states": {"recipe_id": "literal"},
                "source": {"path": "postInit/Crafting.groovy", "line": 2,
                           "column": 1},
                "expression": "crafting.add('example:gear')",
                "lifecycle": {"stage": "postInit"},
            }],
        }
        rows, counts = _identity_rows(compact)
        self.assertEqual(1, counts["duplicate"])
        self.assertEqual(1, counts["declaration"])
        self.assertIn("DUPLICATE RECIPE ID example:gear", rows[0].label)
        self.assertIn("recipe id example:gear", _row_details(rows[0], preview=False))


class MaterialScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_is_explicit_then_keyboard_search_and_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = _Core()
            app = WorkbenchApp(core, preference_path=Path(directory) / "preferences.json")
            async with app.run_test(size=(64, 22)) as pilot:
                app.push_screen(MaterialIdentityScreen("/pack"))
                await pilot.pause()
                self.assertEqual("material-scan", app.focused.id)
                self.assertEqual([], core.calls)
                self.assertLessEqual(app.screen.query_one("#material-native", Button).region.bottom, 22)

                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(1, len(core.calls))
                args, options = core.calls[0]
                self.assertEqual(("groovy", "dev", "--profile", "supersymmetry",
                                  "--source", "/pack", "--identity-summary-json"), args)
                self.assertGreaterEqual(options["timeout"], 300)
                self.assertEqual(3, len(app.screen.query_one("#material-findings", OptionList).options))
                self.assertIn("STATIC duplicate", app.screen.query_one("#material-detail", RichLog).lines[0].text)

                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, MaterialIdentityDetailScreen)
                await pilot.press("r")
                await pilot.pause()
                raw_lines = app.screen.query_one("#material-detail-log", RichLog).lines
                self.assertIn("identity_kind", "\n".join(line.text for line in raw_lines))
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("r")
                await pilot.pause()
                self.assertIsInstance(app.screen, MaterialIdentityDetailScreen)
                self.assertTrue(app.screen.raw)
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("slash", "9", "9", "9", "enter")
                await pilot.pause()
                self.assertIn("No source declaration matches", app.screen.query_one(
                    "#material-detail", RichLog).lines[0].text)


if __name__ == "__main__":
    unittest.main()
