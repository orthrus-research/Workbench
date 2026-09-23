"""Independent structural cases for complete finite impact, not runtime viability."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_atlas_recipe_health import RecipeHealthError, open_recipe_health
from workbench_atlas_recipe_health.complete_impact import derive_complete_recipe_impact, _CompleteImpact


class CompleteRecipeImpactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-complete-impact-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def graph(self, name, rows, *, incomplete=(), observations=False, quests=()):
        """Rows: (label, activity, tuple of OR-alternative tuples, outputs).

        Selectors are required together. Test expectations are literal graph
        facts and do not reuse the production loss or component algorithms.
        """
        graph_name = name
        names = {value for _, _, inputs, outputs in rows for value in outputs}
        names.update(value for _, _, inputs, _ in rows for alternatives in inputs for value in alternatives)
        resources = {name: node_record('forge-fluid', name, {'name': name}) for name in sorted(names)}
        recipes, selectors, edges = {}, {}, []
        nodes = list(resources.values())
        recipe_map = node_record('gt-recipe-map', 'fixture', {'name': 'fixture'})
        machine = node_record('gt-machine', 'fixture:machine', {'tier': 2})
        nodes.extend((recipe_map, machine))
        edges.append(edge_record('uses-recipe-map', machine['id'], recipe_map['id'], {}))
        for name, active, inputs, outputs in rows:
            recipe = node_record('gt-recipe', name, {'recipe_map': 'fixture', 'lookup_active': active,
                                 'duration': 20, 'eut': 30, 'hidden': False})
            recipes[name] = recipe
            nodes.append(recipe)
            edges.append(edge_record('contained-in-recipe-map', recipe['id'], recipe_map['id'], {}))
            for ordinal, alternatives in enumerate(inputs):
                missing = (name, ordinal) in incomplete
                selector = node_record('gt-recipe-input-selector', f'{name}|input|{ordinal}',
                                       {'ordinal': ordinal, 'amount': 1, 'non_consumable': ordinal == 1,
                                        **({'acceptance_complete': False} if missing else {})})
                selectors[(name, ordinal)] = selector
                nodes.append(selector)
                edges.append(edge_record('has-fluid-input-selector', recipe['id'], selector['id'], {'ordinal': ordinal}))
                for index, resource in enumerate(alternatives):
                    edges.append(edge_record('observes-gt-fluid-input-representative' if missing and observations else 'accepts-gt-fluid-input',
                                             selector['id'], resources[resource]['id'], {'amount': 1}, semantic_key=str(index)))
            for ordinal, resource in enumerate(outputs):
                edges.append(edge_record('produces-gt-fluid', recipe['id'], resources[resource]['id'],
                                         {'amount': 1, 'ordinal': ordinal}, semantic_key=str(ordinal)))
        quest_nodes, occurrences = {}, {}
        if quests:
            labels = {label for pair in quests for label in pair}
            quest_nodes = {label: node_record('betterquesting-quest', label, {}) for label in sorted(labels)}
            nodes.extend(quest_nodes.values())
            task = node_record('betterquesting-task-occurrence', 'q0|task', {})
            requirement = node_record('betterquesting-fluid-requirement-occurrence', 'q0|requirement', {})
            nodes.extend((task, requirement))
            edges.extend((edge_record('owns-progression-task', quest_nodes['q0']['id'], task['id'], {}),
                          edge_record('has-progression-fluid-requirement', task['id'], requirement['id'], {}),
                          edge_record('requires-progression-fluid', requirement['id'], resources['product']['id'], {})))
            for ordinal, (dependent, target) in enumerate(quests):
                occurrence = node_record('betterquesting-prerequisite-occurrence', f'{dependent}|{target}|{ordinal}', {})
                occurrences[(dependent, target)] = occurrence
                nodes.append(occurrence)
                edges.extend((edge_record('owns-progression-prerequisite', quest_nodes[dependent]['id'], occurrence['id'], {}),
                              edge_record('targets-progression-prerequisite', occurrence['id'], quest_nodes[target]['id'], {})))
        path = self.root / graph_name
        builder = CategoricalGraphBundleBuilder(path, scope={'fixture': 'complete-impact'}, evidence_binding={'fixture': graph_name})
        builder.add_partition('fixture', classification='independently specified finite graph', dependencies=(),
                              nodes=nodes, edges=edges, evidence_categories=('transformation-recipe',))
        builder.close()
        return path, recipes, resources, selectors, {edge['id']: edge for edge in edges}, quest_nodes, occurrences

    def derive(self, fixture, name='selected'):
        with open_recipe_health(fixture[0]) as view:
            return derive_complete_recipe_impact(view, fixture[1][name]['id'])

    def assert_witness(self, component, edges):
        witness = component['witness']
        members = component['member_node_ids']
        self.assertEqual(sorted(set(members)), members)
        expected = 'workbench-atlas-dependency-component-v2:sha256:' + sha256(json.dumps(members, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(expected, component['component_id'])
        self.assertEqual(witness['node_ids'][0], witness['node_ids'][-1])
        self.assertEqual(len(witness['node_ids']) - 1, len(witness['arcs']))
        self.assertTrue(set(witness['node_ids']) <= set(members))
        for position, arc in enumerate(witness['arcs']):
            self.assertEqual(witness['node_ids'][position:position+2], [arc['source_id'], arc['target_id']])
            original = edges[arc['edge_id']]
            self.assertEqual(original['relation'], arc['relation'])
            endpoints = [original['source'], original['target']]
            if arc['direction'] == 'reverse':
                endpoints.reverse()
            else:
                self.assertEqual('forward', arc['direction'])
            self.assertEqual(endpoints, [arc['source_id'], arc['target_id']])
        self.assertEqual('unknown', component['viability_effect'])

    def test_deep_loss_chain_reaches_fixed_point_and_v1_remains_unchanged(self):
        rows = [('selected', True, (('feed',),), ('r0',))]
        rows.extend((f'chain-{i}', True, ((f'r{i-1}',),), (f'r{i}',)) for i in range(1, 81))
        fixture = self.graph('long', rows)
        with open_recipe_health(fixture[0]) as view:
            old = view.impact(fixture[1]['selected']['id'])
            report = derive_complete_recipe_impact(view, fixture[1]['selected']['id'])
            self.assertEqual(old, view.impact(fixture[1]['selected']['id']))
        self.assertTrue(old['summary']['truncated'])
        self.assertEqual(80, report['summary']['at_risk_recipe_candidate_count'])
        self.assertEqual(81, report['summary']['at_risk_resource_candidate_count'])
        self.assertEqual(80, max(row['depth'] for row in report['propagation']['at_risk_recipes']))
        self.assertEqual(162, report['exploration']['worklist_event_count'])
        self.assertEqual([], report['frontiers'])
        self.assertFalse(report['summary']['truncated'])
        self.assertEqual('complete', report['exploration']['status'])
        self.assertEqual('complete-within-declared-model', report['evidence_completeness']['status'])

    def test_convergent_diamonds_do_not_enumerate_exponential_paths(self):
        rows = [('selected', True, (('external',),), ('r40',))]
        rows.extend((f'{branch}-{i}', True, ((f'r{i-1}',),), (f'r{i}',)) for i in range(1, 41) for branch in ('left', 'right'))
        fixture = self.graph('diamond', rows)
        report = self.derive(fixture)
        self.assertEqual([], report['propagation']['alternative_dependency_components'])
        self.assertEqual([], report['frontiers'])
        self.assertEqual([], report['propagation']['at_risk_recipes'])
        self.assertLessEqual(report['exploration']['visited_node_count'], report['exploration']['graph_node_count'])
        self.assertLessEqual(report['exploration']['traversed_edge_count'], report['exploration']['graph_edge_count'])
        self.assertEqual(1, report['exploration']['worklist_event_count'])

    def test_exact_component_members_witness_and_root_reachability_with_fresh_supply(self):
        rows = [('selected', True, (('feed',),), ('a', 'z')),
                ('a-from-b', True, (('b',),), ('a',)), ('b-from-a', True, (('a',),), ('b',)),
                ('other-a-from-b', True, (('b',),), ('a',)),
                ('fresh-a', True, (('fresh-feed',),), ('a',)), ('z-from-a', True, (('a',),), ('z',))]
        fixture = self.graph('cycle', rows)
        report = self.derive(fixture)
        components = report['propagation']['alternative_dependency_components']
        self.assertEqual(1, len(components))
        recipes, resources, selectors = fixture[1:4]
        expected = {resources[name]['id'] for name in ('a', 'b')}
        for name in ('a-from-b', 'b-from-a', 'other-a-from-b'):
            expected.update((recipes[name]['id'], selectors[(name, 0)]['id']))
        self.assertEqual(expected, set(components[0]['member_node_ids']))
        self.assertEqual(sorted(resources[name]['id'] for name in ('a', 'z')), components[0]['root_resource_ids'])
        self.assert_witness(components[0], fixture[4])
        self.assertLess(len(set(components[0]['witness']['node_ids'])), len(expected))
        self.assertEqual([], report['propagation']['at_risk_resources'])
        self.assertTrue(all(row['producer_portfolio']['viability'] == 'not-assessed' for row in report['direct']['outputs']))
        reverse = self.graph('reverse', list(reversed(rows)))
        self.assertEqual(components, self.derive(reverse)['propagation']['alternative_dependency_components'])

    def test_iterative_scc_handles_a_cycle_deeper_than_python_recursion_limit(self):
        length = 400
        rows = [('selected', True, (('external',),), ('r0',))]
        rows.extend((f'loop-{i}', True, ((f'r{(i+1)%length}',),), (f'r{i}',)) for i in range(length))
        fixture = self.graph('large-cycle', rows)
        report = self.derive(fixture)
        component, = report['propagation']['alternative_dependency_components']
        self.assertEqual(3 * length, len(component['member_node_ids']))
        self.assertEqual(3 * length, len(component['witness']['arcs']))
        self.assert_witness(component, fixture[4])
        self.assertEqual([], report['frontiers'])

    def test_and_selectors_or_alternatives_and_duplicate_edges(self):
        fixture = self.graph('logic', [
            ('selected', True, (('feed',),), ('x', 'x')),
            ('or-input', True, (('x', 'y'),), ('or-output',)),
            ('and-input', True, (('x', 'x'), ('z',)), ('and-output',)),
            ('fresh-y', True, (('feed-y',),), ('y',)),
        ])
        report = self.derive(fixture)
        self.assertEqual([fixture[1]['and-input']['id']], [row['recipe']['selection_id'] for row in report['propagation']['at_risk_recipes']])
        self.assertEqual({fixture[2]['x']['id'], fixture[2]['and-output']['id']}, {row['resource']['selection_id'] for row in report['propagation']['at_risk_resources']})
        self.assertEqual(2, report['summary']['selected_output_count'])
        self.assertEqual(4, report['exploration']['worklist_event_count'])

    def test_inactive_and_unknown_lookup_states_do_not_establish_absence(self):
        inactive = self.graph('inactive', [('selected', True, (('feed',),), ('x',)),
                                          ('inactive', False, (('other',),), ('x',)),
                                          ('inactive-consumer', False, (('x',),), ('tail',))])
        report = self.derive(inactive)
        self.assertEqual(1, report['summary']['at_risk_resource_candidate_count'])
        self.assertEqual([], report['direct']['outputs'][0]['downstream_consumers'])
        unknown = self.graph('unknown', [('selected', True, (('feed',),), ('x',)),
                                        ('unknown', None, (('other',),), ('x',)),
                                        ('unknown-consumer', None, (('x',),), ('tail',))])
        report = self.derive(unknown)
        self.assertEqual([], report['propagation']['at_risk_resources'])
        portfolio = report['direct']['outputs'][0]['producer_portfolio']
        self.assertEqual('producer-evidence-incomplete', portfolio['status'])
        self.assertFalse(portfolio['truncated'])
        self.assertFalse(portfolio['evidence_complete'])
        self.assertEqual(2, report['evidence_completeness']['graph_unknown_lookup_recipe_count'])
        self.assertEqual([], report['frontiers'])
        self.assertEqual('incomplete', report['evidence_completeness']['status'])

    def test_incomplete_and_empty_selectors_are_evidence_gaps_not_resource_limits(self):
        fixture = self.graph('partial', [('selected', True, (('feed',),), ('x',)),
                                        ('partial', True, (('x',),), ('tail',)),
                                        ('empty', True, ((),), ('empty-output',))], incomplete={('partial', 0)})
        report = self.derive(fixture)
        self.assertEqual([], report['direct']['outputs'][0]['downstream_consumers'])
        self.assertEqual([], report['propagation']['at_risk_recipes'])
        self.assertEqual([], report['frontiers'])
        self.assertEqual(2, report['evidence_completeness']['graph_incomplete_selector_count'])
        self.assertIn(fixture[3][('partial', 0)]['id'], report['evidence_completeness']['encountered_incomplete_selector_ids'])
        self.assertIn('selector-acceptance-incomplete', {row['code'] for row in report['unknowns']})
        self.assertFalse(report['summary']['truncated'])
        self.assertEqual('complete', report['exploration']['status'])
        self.assertEqual('evidence-incomplete', report['propagation']['status'])

    def test_inactive_or_unknown_selected_recipe_is_never_a_known_sole_producer(self):
        for activity, status, complete in ((False, 'no-observed-active-producer', True),
                                           (None, 'producer-evidence-incomplete', False)):
            with self.subTest(activity=activity):
                fixture = self.graph('selected-'+str(activity), [('selected', activity, (('feed',),), ('product',))])
                report = self.derive(fixture)
                portfolio = report['direct']['outputs'][0]['producer_portfolio']
                self.assertEqual(status, portfolio['status'])
                self.assertIs(complete, portfolio['evidence_complete'])
                self.assertEqual(0, report['summary']['sole_observed_finite_producer_output_count'])
                self.assertEqual([], report['propagation']['at_risk_resources'])
                self.assertEqual([], report['propagation']['alternative_dependency_components'])
                self.assertEqual([], report['frontiers'])

    def test_selected_self_loop_is_removed_from_alternative_component_graph(self):
        fixture = self.graph('removed-loop', [('selected', True, (('product',),), ('product',)),
                                            ('inactive-loop', False, (('product',),), ('product',))])
        report = self.derive(fixture)
        self.assertEqual([], report['propagation']['alternative_dependency_components'])
        self.assertEqual(1, report['summary']['at_risk_resource_candidate_count'])
        self.assertEqual([], report['propagation']['at_risk_recipes'])

    def test_cancellation_during_loss_worklist_never_returns_completed_report(self):
        rows = [('selected', True, (('feed',),), ('r0',))]
        rows.extend((f'chain-{i}', True, ((f'r{i-1}',),), (f'r{i}',)) for i in range(1, 100))
        fixture = self.graph('cancel-worklist', rows)
        with open_recipe_health(fixture[0]) as view:
            derive_complete_recipe_impact(view, fixture[1]['selected']['id'])
            phase = 'before'
            original = _CompleteImpact.propagate
            def in_worklist(builder):
                nonlocal phase
                phase = 'worklist'
                return original(builder)
            def cancel_worklist():
                if phase == 'worklist':
                    raise InterruptedError('cancel worklist')
            with patch.object(_CompleteImpact, 'propagate', in_worklist), self.assertRaisesRegex(InterruptedError, 'cancel worklist'):
                derive_complete_recipe_impact(view, fixture[1]['selected']['id'], check_cancelled=cancel_worklist)

    def test_quest_dependents_are_exhausted_and_components_have_real_witnesses(self):
        pairs = [(f'q{i}', f'q{i-1}') for i in range(1, 21)] + [('q0', 'q20')]
        fixture = self.graph('quests', [('selected', True, (('feed',),), ('product',))], quests=pairs)
        report = self.derive(fixture)
        quest = report['progression_signals']['quest_signals']
        self.assertEqual(21, len(quest['structural_prerequisite_dependents']))
        depths = {row['quest']['semantic_key']: row['depth'] for row in quest['structural_prerequisite_dependents']}
        self.assertEqual(0, depths['q0'])
        self.assertEqual(20, depths['q20'])
        self.assertEqual(1, len(quest['prerequisite_cycle_components']))
        component = quest['prerequisite_cycle_components'][0]
        self.assertEqual(42, len(component['member_node_ids']))
        self.assertEqual([], component['root_resource_ids'])
        self.assert_witness(component, fixture[4])
        self.assertEqual([], report['frontiers'])
        self.assertEqual('observed-definition-references', quest['status'])

    def test_index_is_immutable_reused_and_not_shared_between_verified_views(self):
        fixture = self.graph('cache', [('selected', True, (('feed',),), ('x',)), ('next', True, (('x',),), ('y',))])
        with open_recipe_health(fixture[0]) as view:
            first = derive_complete_recipe_impact(view, fixture[1]['selected']['id'])
            cached = view._complete_impact_index
            with self.assertRaises(TypeError):
                cached.outgoing[999] = ()
            second = derive_complete_recipe_impact(view, fixture[1]['next']['id'])
            self.assertIs(cached, view._complete_impact_index)
            self.assertEqual(first, derive_complete_recipe_impact(view, fixture[1]['selected']['id']))
        with open_recipe_health(fixture[0]) as view:
            self.assertEqual(second, derive_complete_recipe_impact(view, fixture[1]['next']['id']))
            self.assertIsNot(cached, view._complete_impact_index)

    def test_cancellation_during_index_and_scc_never_returns_completed_report(self):
        rows = [('selected', True, (('feed',),), ('r0',))]
        rows.extend((f'loop-{i}', True, ((f'r{(i+1)%100}',),), (f'r{i}',)) for i in range(100))
        fixture = self.graph('cancel', rows)
        with open_recipe_health(fixture[0]) as view:
            calls = 0
            def cancel_index():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise InterruptedError('cancel index')
            with self.assertRaisesRegex(InterruptedError, 'cancel index'):
                derive_complete_recipe_impact(view, fixture[1]['selected']['id'], check_cancelled=cancel_index)
            self.assertIsNone(getattr(view, '_complete_impact_index', None))
            derive_complete_recipe_impact(view, fixture[1]['selected']['id'])
            cached = view._complete_impact_index
            phase = 'before'
            original = _CompleteImpact.components
            def in_components(builder, *args, **kwargs):
                nonlocal phase
                phase = 'scc'
                return original(builder, *args, **kwargs)
            def cancel_scc():
                if phase == 'scc':
                    raise InterruptedError('cancel scc')
            with patch.object(_CompleteImpact, 'components', in_components), self.assertRaisesRegex(InterruptedError, 'cancel scc'):
                derive_complete_recipe_impact(view, fixture[1]['selected']['id'], check_cancelled=cancel_scc)
            self.assertIs(cached, view._complete_impact_index)

    def test_source_only_nonrecipe_and_invalid_callback_are_refused(self):
        fixture = self.graph('refuse', [('selected', True, (('feed',),), ('x',))])
        with self.assertRaises(RecipeHealthError):
            derive_complete_recipe_impact(object(), 'anything')
        with open_recipe_health(fixture[0]) as view:
            with self.assertRaises(RecipeHealthError):
                derive_complete_recipe_impact(view, fixture[2]['x']['id'])
            with self.assertRaises(RecipeHealthError):
                derive_complete_recipe_impact(view, fixture[1]['selected']['id'], check_cancelled=7)


if __name__ == '__main__':
    unittest.main()
