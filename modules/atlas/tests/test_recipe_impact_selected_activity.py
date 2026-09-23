"""Selected registration activity must agree with direct producer claims."""
from pathlib import Path
import tempfile
import unittest

import test_recipe_cycle_boundaries as fixtures
from workbench_atlas_recipe_health import open_recipe_health


class RecipeImpactSelectedActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-selected-activity-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def report(self, selected_active, others=()):
        root, recipes, _ = fixtures.RecipeCycleBoundaryTests.graph(
            self, 'graph', (('selected', selected_active, (), ('product',)), *others)
        )
        with open_recipe_health(root) as view:
            return view.impact(recipes['selected']['id'], max_depth=4, max_nodes=100)

    def test_inactive_selected_registration_is_not_a_sole_active_producer(self):
        report = self.report(False)
        portfolio = report['direct']['outputs'][0]['producer_portfolio']
        self.assertEqual('no-observed-active-producer', portfolio['status'])
        self.assertFalse(portfolio['truncated'])
        self.assertEqual([], portfolio['alternative_producers'])
        self.assertEqual([], report['propagation']['at_risk_resources'])
        self.assertEqual(0, report['summary']['sole_observed_finite_producer_output_count'])

    def test_inactive_selected_with_active_alternative_retains_that_producer(self):
        report = self.report(False, (('alternative', True, (), ('product',)),))
        portfolio = report['direct']['outputs'][0]['producer_portfolio']
        self.assertEqual('observed-alternatives-present', portfolio['status'])
        self.assertEqual(1, len(portfolio['alternative_producers']))
        self.assertEqual([], report['propagation']['at_risk_resources'])

    def test_unknown_selected_activity_cannot_become_known_zero_or_sole(self):
        report = self.report(None)
        portfolio = report['direct']['outputs'][0]['producer_portfolio']
        self.assertEqual('producer-set-truncated', portfolio['status'])
        self.assertTrue(portfolio['truncated'])
        self.assertEqual([], report['propagation']['at_risk_resources'])
        self.assertIn('producer-lookup-state-unavailable', {row['code'] for row in report['unknowns']})

    def test_active_selected_last_producer_remains_sole(self):
        report = self.report(True, (('inactive', False, (), ('product',)),))
        portfolio = report['direct']['outputs'][0]['producer_portfolio']
        self.assertEqual('sole-observed-finite-producer', portfolio['status'])
        self.assertFalse(portfolio['truncated'])
        self.assertEqual(1, len(report['propagation']['at_risk_resources']))
        self.assertEqual(1, report['summary']['sole_observed_finite_producer_output_count'])


if __name__ == '__main__':
    unittest.main()
