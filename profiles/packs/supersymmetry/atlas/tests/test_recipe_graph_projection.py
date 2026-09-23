"""Independent sealed capture cases for the finite GT recipe profile adapter."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_atlas_categorical_graph import validate_bundle_directory
from workbench_atlas_recipe_health import compare_runtime_recipe_graphs, open_recipe_health
from workbench_profile_supersymmetry import recipe_graphs
from workbench_crucible_runtime_snapshot.capture import RuntimeCaptureError
from workbench_profile_supersymmetry.recipe_graphs import RecipeGraphProjectionError, project_capture


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def item(count=1, *, tag=None, metadata=3):
    return dict(registry_name='fixture:tool', metadata=metadata, item_damage=metadata, count=count, tag=tag)


def fluid(name='fixture:solvent', amount=1000, *, tag=None):
    return dict(fluid_name=name, amount=amount, tag=tag)


def selector(*, stack=None, fluid_stack=None, ordinal=0, amount=1, reusable=False, nbt=False):
    return dict(amount=amount, fluid_stack=fluid_stack, has_nbt_matching_condition=nbt,
                item_stack_representatives=[] if fluid_stack else [stack or item()],
                nbt_condition={'key': 'value'} if nbt else None, nbt_matcher='custom' if nbt else None,
                non_consumable=reusable, ordinal=ordinal, ore_dictionary=False,
                ore_dictionary_id=None, ore_dictionary_name=None,
                runtime_class='gregtech.api.recipes.ingredients.' +
                ('GTRecipeFluidInput' if fluid_stack else 'GTRecipeItemInput'))


def recipe():
    return dict(category='fixture.category', runtime_class='fixture.Recipe', duration=20, eut=30,
                hidden=False, groovy_recipe=True, crafttweaker_recipe=False, properties=[{'heat': 100}],
                item_inputs=[selector(stack=item(7), amount=7, reusable=True)],
                fluid_inputs=[selector(fluid_stack=fluid(), amount=1000)],
                item_outputs=[{'ordinal': 0, 'value': item(2)}],
                fluid_outputs=[{'ordinal': 0, 'value': fluid('fixture:spent', 500)}],
                chanced_item_outputs={'logic_class': 'fixture.Or', 'entries': [
                    {'ordinal': 0, 'value': item(tag={'type': 10, 'value': {'batch': 7}}),
                     'chance': 2500, 'chance_boost': 150, 'runtime_class': 'fixture.Chance'}]},
                chanced_fluid_outputs={'logic_class': 'fixture.Or', 'entries': [
                    {'ordinal': 0, 'value': fluid('fixture:byproduct', 25),
                     'chance': 100, 'chance_boost': 10, 'runtime_class': 'fixture.Chance'}]})


class FiniteRecipeProjectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='finite-recipe-projection-')
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.capture = self.home / 'capture'
        self.capture.mkdir()
        self.inputs = self.home / 'inputs.json'
        self.output = self.home / 'graph'
        self.input_value = dict(capture_id='fixture-capture', launch_id='fixture-launch',
                               physical_side='dedicated_server',
                               pack_binding_id='supersymmetry-foundation-target-binding:sha256:'+'a'*64,
                               platform_binding_id='cleanroom-foundation-target-binding:sha256:'+'b'*64)
        self.categories = {
            'gt-recipes': ('transformation-recipe', []),
            'gt-recipe-maps': ('transformation-recipe-map', [
                {'record_type': 'gt-recipe-map', 'name': 'fixture_map', 'union_identity_count': 1},
                {'record_type': 'gt-recipe-map', 'name': 'empty_map', 'union_identity_count': 0}]),
            'gt-meta-tile-entities': ('machine-core', [
                {'record_type': 'gt-meta-tile-entity', 'registry_name': 'fixture:machine'}]),
            'gt-machine-recipe-maps': ('machine-transformation-binding', [
                {'record_type': 'gt-machine-recipe-map-binding', 'machine': 'fixture:machine',
                 'recipe_map': 'fixture_map'}]),
        }
        self.add_recipe(recipe())

    def add_recipe(self, value, *, duplicate=0):
        self.categories['gt-recipes'][1].append(dict(
            record_type='gt-recipe', recipe_map='fixture_map', semantic_sha256=digest(value),
            duplicate_ordinal=duplicate, lookup_active=True, category_present=True, recipe=value))

    def publish(self, *, category_changes=None):
        self.inputs.write_bytes(encoded(self.input_value))
        binding = dict(capture_id='fixture-capture', launch_id='fixture-launch',
                       input_manifest_sha256=hashlib.sha256(self.inputs.read_bytes()).hexdigest(),
                       candidate_lock_sha256='c'*64, adapter_profile_sha256='d'*64,
                       physical_side='dedicated_server')
        descriptors, payloads = [], []
        for adapter, (category_id, records) in sorted(self.categories.items()):
            records = sorted(deepcopy(records), key=encoded)
            value = dict(format='workbench-crucible-runtime-category-result-v1', schema_version=1,
                         **binding, adapter_id=adapter, category_id=category_id,
                         checkpoint_id='post-start-end-tick', status='complete', stable=True,
                         unsupported_value_count=0, diagnostics=[], records=records,
                         record_count=len(records), records_sha256=digest(records),
                         samples=[dict(ordinal=i, record_count=len(records), records_sha256=digest(records),
                                       unsupported_value_count=0, diagnostics=[]) for i in (1, 2)])
            value.update((category_changes or {}).get(adapter, {}))
            value['result_sha256'] = digest(value)
            raw = encoded(value)
            filename = adapter + '.json'
            (self.capture / filename).write_bytes(raw)
            descriptors.append(dict(adapter_id=adapter, status=value['status'], file=filename))
            payloads.append(dict(file=filename, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        manifest = dict(format='workbench-runtime-graph-raw-bundle-v1', schema_version=1,
                        **binding, categories=descriptors, payloads=payloads)
        manifest['manifest_sha256'] = digest(manifest)
        (self.capture / 'manifest.json').write_bytes(encoded(manifest))
        (self.capture / '.capture-complete').write_bytes(b'')

    def project(self):
        return project_capture(self.capture, self.output, input_manifest=self.inputs)

    def rows(self):
        manifest = json.loads((self.output / 'manifest.json').read_bytes())
        nodes, edges = [], []
        for partition in manifest['partitions']:
            for family, rows in [('nodes', nodes), ('edges', edges)]:
                rows.extend(json.loads(line) for line in (self.output / partition[family]['file']).read_bytes().splitlines())
        return manifest, nodes, edges

    def test_quantity_independent_identity_nbt_chance_reusable_and_custody(self):
        self.publish()
        before = {p: p.read_bytes() for p in (*self.capture.iterdir(), self.inputs)}
        receipt = self.project()
        validate_bundle_directory(self.output)
        manifest, nodes, edges = self.rows()
        self.assertEqual(1, receipt['recipe_count'])
        self.assertEqual(2, receipt['recipe_map_count'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        resources = [n for n in nodes if n['kind'] in {'item-variant', 'forge-fluid'}]
        self.assertEqual(5, len(resources))
        plain_item = next(n for n in resources if n['semantic_key'] == 'fixture:tool|3|3')
        self.assertEqual('captured-item-variant-v1', plain_item['properties']['identity_model'])
        self.assertNotIn('count_in_observation', plain_item['properties'])
        tagged = next(n for n in resources if n['properties'].get('tag'))
        self.assertNotEqual(tagged['id'], plain_item['id'])
        self.assertEqual({'type': 10, 'value': {'batch': 7}}, tagged['properties']['tag'])
        observed = next(e for e in edges if e['relation'] == 'observes-gt-item-input-representative')
        output = next(e for e in edges if e['relation'] == 'produces-gt-item' and not e['properties']['chanced'])
        self.assertEqual(plain_item['id'], observed['target'])
        self.assertEqual(observed['target'], output['target'])
        self.assertEqual((7, True, 2), (observed['properties']['amount'], observed['properties']['non_consumable'], output['properties']['amount']))
        chance = next(e for e in edges if e['relation'] == 'produces-gt-item' and e['properties']['chanced'])
        self.assertEqual((2500, 150, 'fixture.Or'), tuple(chance['properties'][key] for key in ('chance', 'chance_boost', 'logic_class')))
        self.assertTrue(any(e['relation'] == 'produces-gt-fluid' and e['properties']['chanced'] for e in edges))
        self.assertTrue(any(e['relation'] == 'uses-recipe-map' for e in edges))
        self.assertEqual('post-start-end-tick', manifest['scope']['lifecycle_checkpoint_id'])
        self.assertEqual(manifest['scope']['projection_source_sha256'], manifest['evidence_binding']['projection_source_sha256'])
        self.assertEqual(manifest['scope']['projection_format'], manifest['evidence_binding']['projection_format'])
        item_selector = next(n for n in nodes if n['kind'] == 'gt-recipe-input-selector' and n['properties']['runtime_class'].endswith('.GTRecipeItemInput'))
        self.assertFalse(item_selector['properties']['acceptance_complete'])
        self.assertIn('ordinary-item-matching-closure-not-qualified', item_selector['properties']['acceptance_gaps'])
        self.assertEqual(['item-capability-compatibility-not-captured'], item_selector['properties']['matching_limits'])
        node = next(n for n in nodes if n['kind'] == 'gt-recipe')
        raw_record = self.categories['gt-recipes'][1][0]
        self.assertEqual('fixture_map|'+raw_record['semantic_sha256']+'|0', node['semantic_key'])
        self.assertEqual(digest(raw_record), node['evidence'][0]['record_sha256'])
        self.assertEqual(hashlib.sha256(before[self.capture / 'manifest.json']).hexdigest(), manifest['evidence_binding']['capture_manifest_sha256'])

    def test_custom_matchers_wildcards_and_empty_selectors_are_observations(self):
        value = recipe()
        value['item_inputs'] = [selector(stack=item(metadata=32767)), selector(ordinal=1, nbt=True), selector(ordinal=2)]
        value['item_inputs'][2]['item_stack_representatives'] = []
        value['fluid_inputs'] = [selector(fluid_stack=fluid(), amount=1000, nbt=True)]
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selectors = [n for n in nodes if n['kind'] == 'gt-recipe-input-selector']
        self.assertEqual(4, len(selectors))
        self.assertTrue(all(n['properties']['acceptance_complete'] is False for n in selectors))
        self.assertTrue(all(n['properties']['acceptance_gaps'] for n in selectors))
        self.assertFalse(any(e['relation'].startswith('accepts-') for e in edges))
        self.assertEqual(2, sum(e['relation'] == 'observes-gt-item-input-representative' for e in edges))
        self.assertEqual(1, sum(e['relation'] == 'observes-gt-fluid-input-representative' for e in edges))

    def test_zero_quantity_ore_slot_is_retained_with_unresolved_route_quantity(self):
        from workbench_atlas_recipe_health import open_recipe_health, derive_recipe_routes
        value = recipe()
        selected = selector(amount=0)
        selected.update(runtime_class='gregtech.api.recipes.ingredients.GTRecipeOreInput',
                        ore_dictionary=True, ore_dictionary_id=1, ore_dictionary_name='fixtureOre',
                        item_stack_representatives=[])
        value['item_inputs'] = [selected]
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        slot = next(n for n in nodes if n['kind'] == 'gt-recipe-input-selector'
                    and n['properties']['ore_dictionary'])
        self.assertEqual(0, slot['properties']['amount'])
        self.assertEqual([], slot['properties']['captured_item_stack_representatives'])
        self.assertTrue(any(e['relation'] == 'has-item-input-selector' and e['target'] == slot['id'] for e in edges))
        target = next(e['target'] for e in edges if e['relation'] == 'produces-gt-item')
        with open_recipe_health(self.output) as view:
            report = derive_recipe_routes(view, target)
        self.assertEqual('unknown', report['summary']['craftability'])
        self.assertTrue(any(r['code'] == 'selector-quantity-unavailable'
                            and r['selector_id'] == slot['id'] for r in report['unresolved']))

    def test_resealed_semantic_mismatch_and_malformed_quantity_are_refused(self):
        row = self.categories['gt-recipes'][1][0]
        for mutation in ('signature', 'quantity', 'missing_field', 'bool_amount'):
            with self.subTest(mutation=mutation):
                original = deepcopy(row)
                if mutation == 'signature':
                    row['semantic_sha256'] = 'f'*64
                else:
                    if mutation == 'missing_field':
                        del row['recipe']['item_inputs'][0]['non_consumable']
                    else:
                        row['recipe']['item_inputs'][0]['amount'] = -1 if mutation == 'quantity' else True
                    row['semantic_sha256'] = digest(row['recipe'])
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError):
                    self.project()
                self.assertFalse(self.output.exists())
                row.clear()
                row.update(original)

    def test_ore_circuit_and_unknown_matching_cannot_claim_exact_supply_or_exposure(self):
        # OreDictionary.itemMatches ignores unrelated NBT. IntCircuitIngredient
        # compares Configuration, allowing additional tag fields. Neither set
        # equals these captured full-tag resource representatives.
        value = recipe()
        ore = selector()
        ore.update(runtime_class='gregtech.api.recipes.ingredients.GTRecipeOreInput',
                   ore_dictionary=True, ore_dictionary_id=7, ore_dictionary_name='dustFixture')
        circuit = selector(ordinal=1, reusable=True, stack=item(tag={
            'tag_id': 10, 'value': {'Configuration': {'tag_id': 3, 'value': 7}}}))
        circuit['runtime_class'] = 'gregtech.api.recipes.ingredients.IntCircuitIngredient'
        unknown = selector(ordinal=2)
        unknown['runtime_class'] = 'fixture.UnknownItemInput'
        value['item_inputs'] = [ore, circuit, unknown]
        value['fluid_inputs'] = [selector(fluid_stack=fluid(), amount=1000)]
        value['fluid_inputs'][0]['runtime_class'] = 'fixture.UnknownFluidInput'
        value['item_outputs'] = []
        value['chanced_item_outputs']['entries'] = []
        value['chanced_fluid_outputs']['entries'] = []
        producer = recipe()
        producer['item_inputs'] = []
        producer['fluid_inputs'] = []
        producer['item_outputs'] = [{'ordinal': 0, 'value': item(tag={
            'tag_id': 10, 'value': {'unrelated': {'tag_id': 3, 'value': 42}}})}]
        producer['fluid_outputs'] = []
        producer['chanced_item_outputs']['entries'] = []
        producer['chanced_fluid_outputs']['entries'] = []
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)
        self.add_recipe(producer)
        self.categories['gt-recipe-maps'][1][0]['union_identity_count'] = 2
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selectors = [n for n in nodes if n['kind'] == 'gt-recipe-input-selector']
        self.assertEqual(4, len(selectors))
        self.assertTrue(all(n['properties']['acceptance_complete'] is False for n in selectors))
        gaps = {g for n in selectors for g in n['properties']['acceptance_gaps']}
        self.assertEqual({'ore-dictionary-matching-closure-not-projected',
                          'circuit-configuration-matching-closure-not-projected',
                          'unqualified-input-runtime-class'}, gaps)
        self.assertFalse(any(e['relation'].startswith('accepts-') for e in edges))
        self.assertEqual(4, sum(e['relation'].startswith('observes-gt-') for e in edges))
        consumer_node = next(n for n in nodes if n['kind'] == 'gt-recipe' and n['properties']['semantic_sha256'] == digest(value))
        producer_node = next(n for n in nodes if n['kind'] == 'gt-recipe' and n['properties']['semantic_sha256'] == digest(producer))
        with open_recipe_health(self.output) as view:
            consumer_impact = view.impact(consumer_node['id'], max_nodes=200)
            producer_impact = view.impact(producer_node['id'], max_nodes=200)
        self.assertEqual([], consumer_impact['direct']['inputs'])
        self.assertIn('selector-acceptance-incomplete', {r['code'] for r in consumer_impact['unknowns']})
        self.assertEqual([], producer_impact['direct']['outputs'][0]['downstream_consumers'])
        self.assertEqual([], producer_impact['propagation']['at_risk_recipes'])

    def test_changed_projection_interpretation_is_incomparable_even_for_same_capture(self):
        self.publish()
        self.project()
        source_path = Path(recipe_graphs.__file__).resolve()
        original_read = Path.read_bytes
        source_reads = []
        def revised_source(path):
            data = original_read(path)
            if path.resolve() == source_path:
                source_reads.append(path)
                return data + b'\n# another interpretation revision\n'
            return data
        revised = self.home / 'revised'
        with patch.object(Path, 'read_bytes', new=revised_source):
            project_capture(self.capture, revised, input_manifest=self.inputs)
        self.assertEqual([source_path], source_reads)
        with open_recipe_health(self.output) as before, open_recipe_health(revised) as after:
            self.assertNotEqual(before.manifest['scope']['projection_source_sha256'], after.manifest['scope']['projection_source_sha256'])
            report = compare_runtime_recipe_graphs(before, after)
        self.assertEqual('incomparable', report['compatibility']['state'])
        self.assertIn('graph-scope-mismatch', {r['code'] for r in report['compatibility']['blocking_differences']})

    def test_partial_checkpoint_category_profile_and_closure_refusals(self):
        for changes, error in [({'status': 'partial'}, RuntimeCaptureError),
                               ({'checkpoint_id': 'earlier'}, RecipeGraphProjectionError),
                               ({'category_id': 'wrong'}, RecipeGraphProjectionError)]:
            with self.subTest(changes=changes):
                self.publish(category_changes={'gt-recipes': changes})
                with self.assertRaises(error):
                    self.project()
                self.assertFalse(self.output.exists())
        self.input_value['pack_binding_id'] = 'another-pack:sha256:'+'a'*64
        self.publish()
        with self.assertRaises(RecipeGraphProjectionError):
            self.project()
        self.input_value['pack_binding_id'] = 'supersymmetry-foundation-target-binding:sha256:'+'a'*64
        self.categories['gt-machine-recipe-maps'][1][0]['recipe_map'] = 'missing'
        self.publish()
        with self.assertRaises(RecipeGraphProjectionError):
            self.project()
        self.assertFalse(self.output.exists())
        self.assertEqual([], list(self.home.glob('.atlas-recipe-projection-*')))

    def test_missing_category_inconsistent_map_count_and_duplicate_identity_refused(self):
        maps = self.categories['gt-recipe-maps'][1]
        maps[0]['union_identity_count'] = 2
        self.publish()
        with self.assertRaises(RecipeGraphProjectionError):
            self.project()
        self.add_recipe(recipe())
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'duplicated'):
            self.project()
        del self.categories['gt-meta-tile-entities']
        self.publish()
        with self.assertRaises(RuntimeCaptureError):
            self.project()

    def test_existing_output_and_cancellation_preserve_inputs(self):
        self.publish()
        self.output.mkdir()
        sentinel = self.output / 'sentinel'
        sentinel.write_text('retained')
        with self.assertRaises(RecipeGraphProjectionError):
            self.project()
        self.assertEqual('retained', sentinel.read_text())
        def cancel():
            raise InterruptedError('fixture cancellation')
        with self.assertRaises(InterruptedError):
            project_capture(self.capture, self.home / 'cancelled', input_manifest=self.inputs, check_cancelled=cancel)
        self.assertFalse((self.home / 'cancelled').exists())


if __name__ == '__main__':
    unittest.main()
