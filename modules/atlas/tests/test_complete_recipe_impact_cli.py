"""Opt-in complete analysis routing preserves bounded default and cancellation."""
from io import StringIO
from pathlib import Path
import json
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for source in [ROOT / 'api/src', *sorted((ROOT / 'modules').glob('*/src'))]:
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_api import ExecutionContext
from workbench_atlas_recipe_health import cli


def report():
    return {
        'format': 'workbench-atlas-recipe-impact-report-v2',
        'selection': {'selection_id': 'selected'},
        'summary': {'at_risk_resource_candidate_count': 1,
                    'at_risk_recipe_candidate_count': 2,
                    'alternative_dependency_component_count': 3},
        'exploration': {'visited_node_count': 5000, 'traversed_edge_count': 12000},
        'evidence_completeness': {'status': 'incomplete'},
        'unknowns': [{'code': 'selector-acceptance-incomplete', 'message': 'Matching is incomplete.'}],
        'evidence_gaps': [],
    }


class CompleteRecipeImpactCliTests(unittest.TestCase):
    def run_cli(self, *args, **kwargs):
        out, err = StringIO(), StringIO()
        code = cli.main(args, output=out, error=err, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_explicit_complete_route_has_no_bounds_and_forwards_cancellation(self):
        context = ExecutionContext(workspace=ROOT, state_root=ROOT)
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.complete_impact.return_value = report()
        with mock.patch.object(cli, 'open_recipe_health', return_value=fake):
            code, out, err = self.run_cli('impact', '/graph', 'selected',
                                         '--exploration', 'complete-finite', '--json', context=context)
        self.assertEqual(0, code, err)
        self.assertEqual(report(), json.loads(out))
        fake.impact.assert_not_called()
        fake.complete_impact.assert_called_once_with('selected', check_cancelled=context.check_cancelled)

    def test_mixed_resource_modes_refuse_before_opening(self):
        for flag, number in [('--max-depth', '4'), ('--max-nodes', '500')]:
            with self.subTest(flag=flag), mock.patch.object(cli, 'open_recipe_health') as opened:
                code, out, err = self.run_cli('impact', '/graph', 'selected',
                                             '--exploration', 'complete-finite', flag, number, '--json')
            self.assertEqual(2, code)
            self.assertFalse(out)
            self.assertIn('cannot be combined', err)
            opened.assert_not_called()

    def test_default_route_keeps_original_bounds(self):
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.impact.return_value = {}
        with mock.patch.object(cli, 'open_recipe_health', return_value=fake):
            code, _, err = self.run_cli('impact', '/graph', 'selected', '--json')
        self.assertEqual(0, code, err)
        fake.impact.assert_called_once_with('selected', max_depth=4, max_nodes=500)
        fake.complete_impact.assert_not_called()

    def test_cancelled_complete_report_never_emits_success(self):
        context = ExecutionContext(workspace=ROOT, state_root=ROOT)
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        def finish(*args, **kwargs):
            context.cancelled.set()
            return report()
        fake.complete_impact.side_effect = finish
        with mock.patch.object(cli, 'open_recipe_health', return_value=fake):
            code, out, err = self.run_cli('impact', '/graph', 'selected',
                                         '--exploration', 'complete-finite', '--json', context=context)
        self.assertEqual(2, code)
        self.assertFalse(out)
        self.assertIn('cancelled', err)

    def test_human_view_separates_finished_exploration_and_incomplete_evidence(self):
        rendered = cli._render_impact(report())
        self.assertIn('exploration: complete', rendered)
        self.assertIn('Evidence completeness: incomplete', rendered)
        self.assertIn('viability remains unknown', rendered)
        self.assertNotIn('Bounds:', rendered)


if __name__ == '__main__':
    unittest.main()
