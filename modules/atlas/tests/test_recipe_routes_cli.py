"""Public recipe-route command behavior over an independently specified graph."""
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_captured_recipe_routes import quest_target_fixture
from workbench_api import ExecutionContext
from workbench_atlas_recipe_health import cli


class RecipeRoutesCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = quest_target_fixture(self.root / "graph")
        self.selection = self.fixture["resources"]["target"]["id"]

    def query(self, *args, **kwargs):
        out, err = StringIO(), StringIO()
        status = cli.main(["routes", str(self.fixture["path"]), self.selection, *args],
                          output=out, error=err, **kwargs)
        return status, out.getvalue(), err.getvalue()

    def test_json_returns_complete_finite_structure_with_unknown_craftability(self):
        status, out, err = self.query("--json")
        self.assertEqual(0, status, err)
        record = json.loads(out)
        self.assertEqual("workbench-atlas-captured-recipe-routes-v1", record["format"])
        self.assertEqual("complete", record["exploration"]["status"])
        self.assertEqual("unknown", record["summary"]["craftability"])
        self.assertFalse(record["summary"]["truncated"])
        self.assertGreater(record["summary"]["recipe_count"], 1)

    def test_text_keeps_unknowns_and_cycles_prominent(self):
        status, out, err = self.query()
        self.assertEqual(0, status, err)
        self.assertIn("Craftability: unknown", out)
        self.assertIn("jointly required", out)
        self.assertIn("do not supply their own starting materials", out)
        self.assertIn("--json", out)

    def test_explicit_bound_remains_visible(self):
        status, out, err = self.query("--max-recipes", "1", "--json")
        self.assertEqual(0, status, err)
        record = json.loads(out)
        self.assertTrue(record["summary"]["truncated"])
        self.assertGreater(record["summary"]["frontier_count"], 0)

    def test_invalid_bounds_and_recipe_selection_fail(self):
        status, out, err = self.query("--max-resources", "-1")
        self.assertEqual(2, status)
        self.assertEqual("", out)
        self.assertIn("Atlas recipes failed", err)
        self.selection = next(iter(self.fixture["recipes"].values()))["id"]
        status, out, err = self.query()
        self.assertEqual(2, status)
        self.assertEqual("", out)

    def test_source_checkout_cannot_become_runtime_routes(self):
        source = self.root / "source/groovy/postInit/Example.groovy"
        source.parent.mkdir(parents=True)
        source.write_text("// circuit.microprocessor\n")
        out, err = StringIO(), StringIO()
        status = cli.main(["routes", str(self.root / "source"), self.selection, "--json"],
                          output=out, error=err)
        self.assertEqual(2, status)
        self.assertEqual("", out.getvalue())

    def test_cancelled_command_does_not_open_graph(self):
        context = ExecutionContext(self.root, self.root / "state")
        context.cancelled.set()
        with mock.patch.object(cli, "open_recipe_health") as opened:
            status, out, err = self.query(context=context)
        self.assertEqual(2, status)
        self.assertEqual("", out)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
