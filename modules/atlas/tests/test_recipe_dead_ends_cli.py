"""Installed-facing audit exports preserve complete inventory and scope."""
import csv
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_captured_recipe_routes import quest_target_fixture
from workbench_api import ExecutionContext
from workbench_atlas_recipe_health import cli


class RecipeDeadEndsCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-audit-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.graph = quest_target_fixture(self.root / "Graph with spaces 資料")

    def invoke(self, *flags, context=None, path=None):
        out, err = StringIO(), StringIO()
        status = cli.main(["audit-dead-ends", str(path or self.graph['path']), *flags],
                          context=context, output=out, error=err)
        return status, out.getvalue(), err.getvalue()

    def test_json_and_csv_account_for_every_recipe_and_exact_findings(self):
        status, raw, err = self.invoke("--json")
        self.assertEqual(0, status, err)
        report = json.loads(raw)
        self.assertEqual("workbench-atlas-recipe-dead-ends-v1", report["format"])
        self.assertEqual(3, report["summary"]["recipe_count"])
        self.assertFalse(report["summary"]["truncated"])
        status, raw, err = self.invoke("--csv")
        self.assertEqual(0, status, err)
        rows = list(csv.DictReader(StringIO(raw)))
        self.assertEqual(len(report["recipes"]), len(rows))
        for exported, source in zip(rows, report["recipes"]):
            self.assertEqual(source['selection_id'], exported['selection_id'])
            self.assertEqual(source['semantic_key'], exported['semantic_key'])
            self.assertEqual(report['context']['graph_set_id'], exported['graph_set_id'])
            self.assertEqual(report['policy']['id'], exported['policy_id'])
            self.assertEqual(report['policy']['implementation_sha256'], exported['policy_implementation_sha256'])
            for key in ('recipe_maps', 'recipe_values', 'findings', 'issues', 'input_inventory', 'upstream', 'downstream', 'inputs', 'outputs', 'cycle_component_ids'):
                self.assertEqual(source[key], json.loads(exported[key]))
            self.assertIn("unknown", exported['scope'])

    def test_human_output_identifies_summary_and_unknown_external_coverage(self):
        status, text, err = self.invoke()
        self.assertEqual(0, status, err)
        for phrase in ("Recipes audited: 3", "unknown", "Summary only", "--json", "--csv", "starting supply"):
            self.assertIn(phrase, text)

    def test_source_only_is_refused_without_success_output(self):
        source = self.root / 'source/groovy/postInit/recipes.groovy'
        source.parent.mkdir(parents=True)
        source.write_text('// recipe source only\n')
        status, raw, err = self.invoke('--json', path=self.root/'source')
        self.assertEqual(2, status)
        self.assertEqual('', raw)
        self.assertIn('Atlas recipes failed', err)

    def test_cancelled_command_starts_no_graph_read_or_export(self):
        context = ExecutionContext(self.root, self.root/'state')
        context.cancelled.set()
        with mock.patch.object(cli, 'open_recipe_health') as opening:
            status, raw, err = self.invoke('--csv', context=context)
        self.assertEqual(2, status)
        self.assertEqual('', raw)
        opening.assert_not_called()

    def test_cancellation_reaches_initial_graph_verification(self):
        from workbench_atlas_recipe_health import view
        context = ExecutionContext(self.root, self.root/'state')

        def verifying(path, *, rebuild_if_missing, check_cancelled):
            self.assertIsNotNone(check_cancelled)
            context.cancelled.set()
            check_cancelled()
            self.fail('verification continued after cancellation')

        with mock.patch.object(view, 'GraphRecipeHealthView', side_effect=verifying) as opening:
            status, raw, err = self.invoke('--json', context=context)
        self.assertEqual(2, status)
        self.assertEqual('', raw)
        opening.assert_called_once()


if __name__ == '__main__':
    unittest.main()
