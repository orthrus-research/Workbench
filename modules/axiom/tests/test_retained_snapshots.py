"""Retained recipe scope and local graph references are independent of counts."""

from copy import deepcopy
import unittest
from unittest.mock import patch
from workbench_core.check_snapshot_contract import scope_identity
from workbench_core.check_snapshot_evolution import derived_view

from workbench_axiom import retained_snapshots as snapshots


class RetainedSnapshotMeaningTests(unittest.TestCase):
    def test_historical_scope_does_not_depend_on_current_producer_declarations(self):
        request = self.request()
        historical = snapshots.scope(request)
        manifest = {'producer': {'id': 'axiom'}, 'scope_id': scope_identity(historical)}
        with patch.object(snapshots, 'declarations', side_effect=AssertionError('current producer must not select old obligations')):
            self.assertEqual(historical, snapshots.resolve_scope(request, manifest, scope_identity=scope_identity))
        manifest['scope_id'] = 'unknown-future-scope'
        self.assertIsNone(snapshots.resolve_scope(request, manifest, scope_identity=scope_identity))

    def test_old_and_new_overviews_bind_reader_without_inventing_missing_fields(self):
        old = {'format': snapshots.OVERVIEW_V1, 'native': {'status': 'native-failed'},
               'failure_type': None, 'detail_state': 'not-loaded'}
        before = deepcopy(old)
        def read(value, identity):
            return snapshots.read_overview(value, identity, derive_view=derived_view)
        view = read(old, 'snapshot-a')
        self.assertEqual('loaded', view['state'])
        self.assertEqual(old['native'], view['view']['native'])
        self.assertEqual(snapshots.OVERVIEW_V2, view['view']['format'])
        self.assertNotEqual(view['binding']['id'], read(old, 'snapshot-b')['binding']['id'])
        self.assertEqual('loaded', read(view['view'], 'snapshot-a')['state'])
        del old['native']
        self.assertEqual('incomplete', read(old, 'snapshot-a')['state'])
        self.assertEqual('unsupported-schema', read({'format': 'future'}, 'snapshot-a')['state'])
        self.assertIn('native', before)

    def test_same_reference_with_changed_child_and_renumbered_graphs(self):
        root = {'output': {'nativeValueRef': 'a'}}
        before = {'a': {'count': 4, 'self': {'nativeValueRef': 'a'}}}
        after = {'a': {'count': 5, 'self': {'nativeValueRef': 'a'}}}
        self.assertEqual('changed', snapshots.compare_graph(root, root, before.__getitem__, after.__getitem__)['state'])
        renamed = {'b': {'count': 4, 'self': {'nativeValueRef': 'b'}}}
        self.assertEqual('unchanged', snapshots.compare_graph(root, {'output': {'nativeValueRef': 'b'}},
            before.__getitem__, renamed.__getitem__)['state'])
        self.assertEqual('incompatible', snapshots.compare_graph(root, root, before.__getitem__, {}.__getitem__)['state'])

    def test_graph_duplicates_aliases_order_types_and_missing_fields_remain_distinct(self):
        def compare(a, b, left=None, right=None):
            return snapshots.compare_graph(a, b, (left or {}).__getitem__, (right or {}).__getitem__)['state']
        self.assertEqual('changed', compare([1, 1], [1]))
        self.assertEqual('changed', compare([1, 2], [2, 1]))
        self.assertEqual('changed', compare(True, 1))
        self.assertEqual('changed', compare(1, 1.0))
        self.assertEqual('changed', compare(-0.0, 0.0))
        self.assertEqual('incompatible', compare({'count': 1}, {}))
        left = [{'nativeValueRef': 'a'}, {'nativeValueRef': 'a'}]
        right = [{'nativeValueRef': 'b'}, {'nativeValueRef': 'c'}]
        self.assertEqual('changed', compare(left, right, {'a': 4}, {'b': 4, 'c': 4}))

    def request(self, stage='recipes'):
        return {'inputs': {'context': {'initializationStage': stage}}, 'baseline': None}

    def test_selected_recipe_stage_requires_recipe_sections_even_when_observer_omits_them(self):
        selected = snapshots.scope(self.request())
        self.assertTrue(selected['sections']['crafting-recipes'])
        self.assertTrue(selected['sections']['crafting-values'])
        self.assertTrue(selected['sections']['gt-recipes'])
        self.assertFalse(snapshots.scope(self.request('preInit'))['sections']['gt-recipes'])
        self.assertTrue(snapshots.scope(self.request('preInit'))['sections']['diagnostics'])

    def test_same_run_local_id_in_separate_native_catalogs_preserves_both_meanings(self):
        value = {'first': {'nativeValues': {'v1': {'value': 1}}, 'recipe': {'nativeValueRef': 'v1'}},
                 'second': {'nativeValues': {'v1': {'value': 2}}, 'recipe': {'nativeValueRef': 'v1'}}}
        before = deepcopy(value)
        snapshots.verify_references(value)
        self.assertEqual(before, value)
        value['second']['nativeValues'].clear()
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            snapshots.verify_references(value)

    def test_aliases_and_cycles_resolve_without_executing_or_flattening_objects(self):
        value = {'nativeValues': {'a': {'child': {'nativeValueRef': 'b'}},
                                  'b': {'child': {'nativeValueRef': 'a'}}},
                 'recipes': [{'nativeValueRef': 'a'}, {'nativeValueRef': 'a'}]}
        snapshots.verify_references(value)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            snapshots.verify_references({'nativeValueRef': 'a'})

    def test_paired_requests_have_distinct_baseline_and_candidate_section_obligations(self):
        request = self.request()
        request['baseline'] = {'fixture': True}
        selected = snapshots.scope(request)['sections']
        for side in ('baseline', 'candidate'):
            self.assertTrue(selected[side + '-crafting-recipes'])
            self.assertTrue(selected[side + '-crafting-values'])

    def test_overview_preserves_each_native_outcome_without_loading_details(self):
        bad = {'status': 'source-error', 'result': {'nativeOutcome': 'native-failed',
               'initialization': {'status': 'native-failed', 'recipeEffectsChecked': True},
               'execution': {'diagnostics': ['complete original message']}}}
        good = {'status': 'accepted', 'result': {'nativeOutcome': 'completed',
                'initialization': {'status': 'completed'}, 'expectations': {'status': 'not-requested', 'checks': []}}}
        record = {'failure': None, 'native': {'status': 'accepted', 'result': {'baseline': bad, 'candidate': good}}}
        original = deepcopy(record)
        view = snapshots.overview(record)
        self.assertEqual('source-error', view['native']['result']['baseline']['status'])
        self.assertEqual('completed', view['native']['result']['candidate']['result']['nativeOutcome'])
        self.assertNotIn('execution', view['native']['result']['baseline']['result'])
        self.assertNotIn('checks', view['native']['result']['candidate']['result']['expectations'])
        self.assertEqual('not-loaded', view['detail_state'])
        self.assertEqual(original, record)


if __name__ == '__main__':
    unittest.main()
