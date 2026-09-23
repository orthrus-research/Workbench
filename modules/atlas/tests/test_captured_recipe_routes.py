"""Independent categorical fixtures for captured recipe prerequisite routes.

These are synthetic contract cases, not observations of a particular modpack.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_atlas_recipe_health import RecipeHealthError, RecipeRouteOptions, derive_recipe_routes, open_recipe_health


def route_graph(path, rows, *, extra_resources=(), observation_authority=False, machine_binding=True):
    """Build an independently specified graph, returning exact canonical rows."""
    resources, recipes, selectors, nodes, edges = {}, {}, {}, [], []

    def resource(name):
        if name not in resources:
            kind = 'forge-fluid' if name.startswith('fluid:') else 'item-variant'
            resources[name] = node_record(kind, name, {'label': name, 'metadata': 0, 'tag': None})
            nodes.append(resources[name])
        return resources[name]

    for name in extra_resources:
        resource(name)
    recipe_map = node_record('gt-recipe-map', 'fixture:assembler', {'name': 'fixture:assembler'})
    machine = node_record('gt-machine', 'fixture:machine', {'tier': 2})
    nodes.extend((recipe_map, machine))
    if machine_binding:
        edges.append(edge_record('uses-recipe-map', machine['id'], recipe_map['id'], {'observed': True}))
    for row in rows:
        name = row['name']
        recipe = node_record('gt-recipe', name, {
            'lookup_active': row.get('active', True), 'duration': row.get('duration', 200),
            'eut': row.get('eut', 120), 'properties': row.get('properties', {'cleanroom': 'CLEANROOM'}),
            **({'captured_input_counts': row.get('input_counts', {
                'item': sum(not value.get('fluid') for value in row.get('inputs', [])),
                'fluid': sum(bool(value.get('fluid')) for value in row.get('inputs', []))})}
               if row.get('input_inventory', True) else {})},
            [{'fixture': name, 'record_ordinal': len(recipes)}])
        nodes.append(recipe)
        recipes[name] = recipe
        edges.append(edge_record('contained-in-recipe-map', recipe['id'], recipe_map['id'], {}))
        for position, raw in enumerate(row.get('inputs', [])):
            props = {'ordinal': position, 'amount': raw.get('amount', 1),
                     'non_consumable': raw.get('reusable', False),
                     **({'acceptance_complete': raw['complete']} if 'complete' in raw else {}),
                     **raw.get('properties', {})}
            selector = node_record('gt-recipe-input-selector', f'{name}|{position}', props,
                                   [{'fixture': name, 'selector_ordinal': position}])
            selectors[(name, position)] = selector
            nodes.append(selector)
            family = 'fluid' if raw.get('fluid') else 'item'
            edges.append(edge_record(f'has-{family}-input-selector', recipe['id'], selector['id'], {'ordinal': position}))
            for observed in (False, True):
                for ordinal, target in enumerate(raw.get('observed' if observed else 'members', [])):
                    relation = ('observes-gt-fluid-input-representative' if family == 'fluid' else 'observes-gt-item-input-representative') if observed else (
                        'accepts-gt-fluid-input' if family == 'fluid' else 'accepts-gt-item-alternative')
                    edges.append(edge_record(relation, selector['id'], resource(target)['id'],
                                             {'amount': props['amount'], 'non_consumable': props['non_consumable'],
                                              'representative_ordinal': ordinal}, semantic_key=str(ordinal)))
        for ordinal, raw in enumerate(row.get('outputs', [])):
            raw = {'resource': raw} if isinstance(raw, str) else raw
            target = resource(raw['resource'])
            props = {'amount': 1, 'ordinal': ordinal, 'output_family': 'item_outputs', 'chanced': False,
                     **{key: value for key, value in raw.items() if key != 'resource'}}
            edges.append(edge_record('produces-gt-fluid' if target['kind'] == 'forge-fluid' else 'produces-gt-item',
                                     recipe['id'], target['id'], props,
                                     [{'fixture': name, 'output_ordinal': ordinal}],
                                     semantic_key=f"{props['output_family']}:{props['ordinal']}"))
    kwargs = {'evidence_authority': 'retained-observations-v1'} if observation_authority else {}
    builder = CategoricalGraphBundleBuilder(path, scope={'fixture': 'captured-routes', 'physical_side': 'CLIENT'},
                                           evidence_binding={'fixture': 'independent contract evidence'}, **kwargs)
    builder.add_partition('fixture', classification='synthetic contract graph', dependencies=(),
                          nodes=nodes, edges=edges, evidence_categories=('transformation-recipe',),
                          limitations=('Fixture is not a native game observation.',))
    builder.close()
    return {'path': path, 'recipes': recipes, 'resources': resources, 'selectors': selectors,
            'edges': {edge['id']: edge for edge in edges}}


def quest_target_fixture(path):
    """Quest-authoring shape with MV final batch and HV upstream gates."""
    inputs = [{'members': [name], 'complete': True} for name in
              ('board', 'cpu', 'dram', 'uart', 'rom', 'clock')]
    inputs.extend(({'members': ['resistor-a', 'resistor-b'], 'amount': 2, 'complete': True},
                   {'members': ['capacitor'], 'amount': 2, 'complete': True},
                   {'members': ['pin'], 'amount': 12, 'complete': True},
                   {'members': ['fluid:paste'], 'amount': 144, 'fluid': True, 'complete': True},
                   {'members': ['config-1'], 'complete': True, 'reusable': True}))
    return route_graph(path, [
        {'name': 'final', 'inputs': inputs, 'outputs': [{'resource': 'target', 'amount': 8}], 'eut': 120},
        {'name': 'cpu', 'inputs': [{'members': ['wafer'], 'complete': True},
                                  {'members': ['mask'], 'complete': True, 'reusable': True}],
         'outputs': ['cpu'], 'eut': 480},
        {'name': 'paste', 'inputs': [{'members': ['solder'], 'amount': 9, 'complete': True},
                                    {'members': ['fluid:flux'], 'amount': 1000, 'complete': True, 'fluid': True}],
         'outputs': [{'resource': 'fluid:paste', 'amount': 1440, 'output_family': 'fluid_outputs'}], 'eut': 480},
    ])


class CapturedRecipeRoutesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-routes-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def report(self, graph, target='target', **kwargs):
        with open_recipe_health(graph['path']) as view:
            return derive_recipe_routes(view, graph['resources'][target]['id'], **kwargs)

    def test_target_batch_and_or_requirements_reuse_and_gates_remain_distinct(self):
        graph = quest_target_fixture(self.root / 'graph')
        report = self.report(graph)
        self.assertEqual('complete', report['exploration']['status'])
        self.assertEqual('complete-finite', report['exploration']['mode'])
        self.assertEqual(3, report['summary']['recipe_count'])
        final = next(row for row in report['routes']['items'] if row['producer']['semantic_key'] == 'final')
        self.assertEqual(8, final['production']['matched_outputs'][0]['relationship']['properties']['amount'])
        self.assertEqual('AND', final['ingredient_slots']['mode'])
        self.assertEqual(10, len(final['ingredient_slots']['slots']))
        resistor = next(row for row in final['ingredient_slots']['slots']
                        if row['slot']['id'] == graph['selectors'][('final', 6)]['id'])
        self.assertEqual(2, resistor['semantics']['quantity'])
        self.assertEqual('OR', resistor['alternatives']['mode'])
        self.assertEqual(2, len(resistor['alternatives']['items']))
        self.assertEqual(1, len(final['reusable_requirements']['slots']))
        self.assertEqual(120, final['producer']['properties']['eut'])
        self.assertEqual({120, 480}, {row['producer']['properties']['eut'] for row in report['routes']['items']})
        self.assertEqual('unknown', report['summary']['craftability'])
        self.assertEqual('unknown', report['assumptions']['existing_infrastructure'])
        self.assertTrue(all(row['mechanics']['execution_status'] == 'unassessed' for row in report['routes']['items']))
        self.assertTrue(any(row['code'] == 'no-observed-producer' and row['resource_id'] == graph['resources']['mask']['id'] for row in report['unresolved']))
        self.assertEqual('AND', report['prerequisites']['operations'][0]['requirements']['mode'])

    def test_same_resource_input_occurrences_and_large_exact_quantities_are_not_merged(self):
        huge = 9007199254740993
        graph = route_graph(self.root / 'graph', [{'name': 'r', 'inputs': [
            {'members': ['feed'], 'amount': 2, 'complete': True},
            {'members': ['feed'], 'amount': huge, 'complete': True}],
            'outputs': [{'resource': 'target', 'amount': huge}]}])
        route = self.report(graph)['routes']['items'][0]
        self.assertEqual([2, huge], [row['semantics']['quantity'] for row in route['ingredient_slots']['slots']])
        alternatives = [row['alternatives']['items'][0] for row in route['ingredient_slots']['slots']]
        self.assertEqual(alternatives[0]['subproblem_id'], alternatives[1]['subproblem_id'])
        self.assertEqual(huge, route['production']['matched_outputs'][0]['relationship']['properties']['amount'])

    def test_shared_machine_map_is_queried_once_per_request_including_missing_bindings(self):
        for machine_binding in (True, False):
            with self.subTest(machine_binding=machine_binding):
                graph = route_graph(self.root / str(machine_binding), [
                    {'name': 'final', 'inputs': [{'members': ['a', 'b'], 'complete': True}], 'outputs': ['target']},
                    {'name': 'a', 'outputs': ['a']}, {'name': 'b', 'outputs': ['b']}],
                    machine_binding=machine_binding)
                with open_recipe_health(graph['path']) as view:
                    reports = []
                    for _ in range(2):
                        statements = []
                        view.query.connection.set_trace_callback(statements.append)
                        reports.append(derive_recipe_routes(view, graph['resources']['target']['id']))
                        view.query.connection.set_trace_callback(None)
                        binding_queries = [sql for sql in statements if
                            'currently-selects-recipe-map' in sql and 'uses-recipe-map' in sql]
                        self.assertEqual(1, len(binding_queries))
                self.assertEqual(reports[0], reports[1])
                report = reports[0]
                self.assertEqual(3, report['summary']['recipe_count'])
                self.assertTrue(all(len(row['mechanics']['recipe_map_bindings'][0]['bindings']) == int(machine_binding)
                                    for row in report['routes']['items']))
                issues = [row for row in report['unresolved'] if row['code'] == 'machine-map-binding-unavailable']
                self.assertEqual(0 if machine_binding else 3, len(issues))

    def test_bounded_frontiers_do_not_mark_unrelated_missing_resources_truncated(self):
        graph = route_graph(self.root / 'graph', [
            {'name': 'final', 'inputs': [{'members': ['a', 'missing', 'b'], 'complete': True}], 'outputs': ['target']},
            {'name': 'a', 'outputs': ['a']}, {'name': 'b', 'outputs': ['b']}])
        report = self.report(graph, options=RecipeRouteOptions(max_depth=1))
        self.assertEqual({'target': 'expanded', 'a': 'truncated', 'b': 'truncated', 'missing': 'unresolved'},
                         {row['target']['semantic_key']: row['status'] for row in report['subproblems']})
        self.assertEqual({graph['resources'][name]['id'] for name in ('a', 'b')},
                         {row['resource_id'] for row in report['frontiers']})

    def test_ordered_chance_outputs_and_guaranteed_cooutputs_are_lossless(self):
        graph = route_graph(self.root / 'graph', [{'name': 'cut', 'outputs': [
            {'resource': 'target', 'amount': 64}, {'resource': 'seed', 'amount': 1},
            {'resource': 'dust', 'amount': 4, 'output_family': 'chanced_item_outputs', 'ordinal': 0,
             'chanced': True, 'logic_class': 'XOR', 'chance': 5000, 'chance_boost': 0},
            {'resource': 'target', 'amount': 8, 'output_family': 'chanced_item_outputs', 'ordinal': 1,
             'chanced': True, 'logic_class': 'XOR', 'chance': 10000, 'chance_boost': 0}]}])
        report = self.report(graph)
        self.assertEqual(1, report['summary']['route_count'])
        production = report['routes']['items'][0]['production']
        self.assertEqual(2, len(production['matched_outputs']))
        chances = [row['relationship']['properties'] for row in production['all_outputs'] if row['relationship']['properties']['chanced']]
        self.assertEqual([5000, 10000], [row['chance'] for row in chances])
        self.assertEqual([0, 1], [row['ordinal'] for row in chances])
        self.assertEqual(['XOR', 'XOR'], [row['logic_class'] for row in chances])
        self.assertEqual({edge['id'] for edge in graph['edges'].values() if edge['relation'].startswith('produces-')},
                         {row['relationship']['id'] for row in production['all_outputs']})

    def test_complete_empty_and_incomplete_empty_and_observations_are_distinct(self):
        graph = route_graph(self.root / 'graph', [
            {'name': 'r', 'inputs': [{'members': [], 'complete': True}, {'members': [], 'complete': False},
                                    {'observed': ['representative'], 'complete': False}], 'outputs': ['target']},
            {'name': 'unqualified', 'outputs': ['representative']}])
        report = self.report(graph)
        self.assertEqual(1, report['summary']['recipe_count'])
        slots = report['routes']['items'][0]['ingredient_slots']['slots']
        self.assertEqual([True, False, False], [row['alternatives']['exhaustive'] for row in slots])
        self.assertEqual([], slots[2]['alternatives']['items'])
        self.assertEqual(1, len(slots[2]['observed_representatives']))
        codes = [row['code'] for row in report['unresolved']]
        self.assertEqual(1, codes.count('no-accepted-resource-in-captured-domain'))
        self.assertEqual(2, codes.count('selector-acceptance-incomplete'))
        self.assertFalse(report['summary']['truncated'])

    def test_unknown_and_inactive_lookup_are_visible_but_not_routes(self):
        graph = route_graph(self.root / 'graph', [
            {'name': 'active', 'outputs': ['target']},
            {'name': 'inactive', 'active': False, 'outputs': ['target']},
            {'name': 'unknown', 'active': None, 'outputs': ['target']}])
        report = self.report(graph)
        self.assertEqual(['active'], [row['producer']['semantic_key'] for row in report['routes']['items']])
        self.assertEqual({'lookup-inactive-producer', 'producer-lookup-state-unknown'}, {row['code'] for row in report['unresolved']})

    def test_qualified_finite_domain_and_residual_matching_limits_survive(self):
        properties = {'acceptance_model': 'gt-2.8.10-forge-1.12.2-finite-item-matching-v1',
                      'acceptance_domain': {'scope': 'captured-gt-input-output-item-variants',
                                            'sha256': 'f' * 64, 'count': 3},
                      'matching_state': {'matcher': 'fixture', 'matching_configurations': [1], 'integrated_circuit': True},
                      'captured_item_stack_representatives': [{'fixture': 'original representative'}],
                      'matching_limits': ['item-capability-compatibility-not-captured']}
        graph = route_graph(self.root / 'graph', [{'name': 'r', 'inputs': [
            {'members': ['circuit-with-extra-tag'], 'complete': True, 'reusable': True, 'properties': properties}],
            'outputs': ['target']}])
        report = self.report(graph)
        slot = report['routes']['items'][0]['reusable_requirements']['slots'][0]
        self.assertEqual(graph['selectors'][('r', 0)], slot['slot'])
        self.assertTrue(slot['alternatives']['exhaustive'])
        self.assertEqual('captured-resource-domain', slot['alternatives']['scope'])
        self.assertTrue(any(row['code'] == 'selector-runtime-matching-limits' for row in report['unresolved']))

    def test_empty_selector_inventory_and_missing_machine_binding_remain_gaps(self):
        graph = route_graph(self.root / 'graph', [{'name': 'legacy', 'outputs': ['target'], 'input_inventory': False}],
                            machine_binding=False)
        report = self.report(graph)
        codes = {row['code'] for row in report['unresolved']}
        self.assertIn('recipe-input-inventory-unavailable', codes)
        self.assertIn('machine-map-binding-unavailable', codes)
        self.assertNotIn('recipe-map-binding-unavailable', codes)
        self.assertEqual(1, report['summary']['route_count'])
        graph = route_graph(self.root / 'mismatch', [{'name': 'bad', 'outputs': ['target'],
                                                     'input_counts': {'item': 1, 'fluid': 0}}])
        with self.assertRaisesRegex(RecipeHealthError, 'counts differ'):
            self.report(graph)

    def test_cycle_witness_has_exact_edges_with_seed_and_viability_unknown(self):
        graph = route_graph(self.root / 'graph', [
            {'name': 'from-seed', 'inputs': [{'members': ['seed'], 'complete': True}], 'outputs': ['target']},
            {'name': 'seed-return', 'inputs': [{'members': ['target'], 'complete': True}], 'outputs': ['seed']},
            {'name': 'fresh-seed', 'inputs': [{'members': ['external'], 'complete': True}], 'outputs': ['seed']}])
        report = self.report(graph)
        self.assertEqual(1, len(report['cycles']))
        component = report['cycles'][0]
        self.assertEqual('unknown', component['seed_supply'])
        self.assertEqual('unknown', component['viability_effect'])
        witness = component['witness']
        self.assertEqual(witness['node_ids'][0], witness['node_ids'][-1])
        for index, arc in enumerate(witness['arcs']):
            original = graph['edges'][arc['edge_id']]
            endpoints = [original['source'], original['target']]
            if arc['direction'] == 'reverse':
                endpoints.reverse()
            self.assertEqual(endpoints, witness['node_ids'][index:index + 2])
        self.assertIn('fresh-seed', [row['producer']['semantic_key'] for row in report['routes']['items']])

    def test_deep_finite_chain_and_shared_diamonds_do_not_stop_at_legacy_limits(self):
        rows = [{'name': 'r0', 'outputs': ['v0']}]
        rows.extend({'name': f'{side}{n}', 'inputs': [{'members': [f'v{n-1}'], 'complete': True}], 'outputs': [f'v{n}']}
                    for n in range(1, 90) for side in ('a', 'b'))
        graph = route_graph(self.root / 'graph', rows)
        report = self.report(graph, 'v89')
        self.assertEqual(179, report['summary']['recipe_count'])
        self.assertEqual(90, report['summary']['resource_count'])
        self.assertEqual([], report['frontiers'])
        self.assertEqual([], report['cycles'])

    def test_cycle_larger_than_python_recursion_limit_remains_finite(self):
        graph = route_graph(self.root / 'graph', [
            {'name': f'r{n}', 'inputs': [{'members': [f'v{(n+1)%400}'], 'complete': True}],
             'outputs': [f'v{n}']} for n in range(400)])
        report = self.report(graph, 'v0')
        self.assertEqual(1, len(report['cycles']))
        self.assertEqual(1200, len(report['cycles'][0]['member_node_ids']))
        self.assertEqual(1200, len(report['cycles'][0]['witness']['arcs']))
        self.assertFalse(report['summary']['truncated'])

    def test_explicit_limits_publish_frontiers_without_silent_drop(self):
        for options, code in ((RecipeRouteOptions(max_depth=0), 'max-depth'),
                              (RecipeRouteOptions(max_resources=1), 'max-resources'),
                              (RecipeRouteOptions(max_recipes=1), 'max-recipes')):
            with self.subTest(options=options):
                graph = quest_target_fixture(self.root / code)
                report = self.report(graph, options=options)
                self.assertEqual('truncated', report['exploration']['status'])
                self.assertTrue(any(row['code'] == code for row in report['frontiers']))
                self.assertEqual('unknown', report['summary']['craftability'])

    def test_cancellation_during_traversal_leaves_view_reusable(self):
        graph = quest_target_fixture(self.root / 'graph')
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 20:
                raise InterruptedError('stop')
        with open_recipe_health(graph['path']) as view:
            with self.assertRaisesRegex(InterruptedError, 'stop'):
                derive_recipe_routes(view, graph['resources']['target']['id'], check_cancelled=cancel)
            report = derive_recipe_routes(view, graph['resources']['target']['id'])
        self.assertEqual(3, report['summary']['recipe_count'])

    def test_exact_selection_source_and_initialization_authority_refusals(self):
        graph = quest_target_fixture(self.root / 'graph')
        with open_recipe_health(graph['path']) as view:
            with self.assertRaises(RecipeHealthError):
                derive_recipe_routes(view, graph['recipes']['final']['id'])
            with self.assertRaises(RecipeHealthError):
                derive_recipe_routes(view, 'not-in-this-graph')
            first = derive_recipe_routes(view, graph['resources']['target']['id'])
            self.assertEqual(first, derive_recipe_routes(view, graph['resources']['target']['id']))
        with self.assertRaises(RecipeHealthError):
            derive_recipe_routes(object(), 'anything')
        graph = route_graph(self.root / 'observation', [], extra_resources=('target',), observation_authority=True)
        from workbench_atlas_recipe_health.view import GraphRecipeHealthView
        with GraphRecipeHealthView(graph['path']) as view:
            with self.assertRaisesRegex(RecipeHealthError, 'not promoted'):
                derive_recipe_routes(view, graph['resources']['target']['id'])

    def test_options_reject_boolean_and_invalid_bounds(self):
        for kwargs in ({'max_depth': True}, {'max_resources': 0}, {'max_recipes': -1}):
            with self.assertRaises(RecipeHealthError):
                RecipeRouteOptions(**kwargs)


if __name__ == '__main__':
    unittest.main()
