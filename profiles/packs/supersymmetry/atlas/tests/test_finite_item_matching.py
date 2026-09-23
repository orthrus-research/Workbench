"""Independent finite matcher cases; expected alternatives are literal fixtures.

These tests prove admission and projection behavior, not a native game run.
"""
from copy import deepcopy
import struct
import unittest
from workbench_profile_supersymmetry.recipe_graphs import project_capture

from workbench_profile_supersymmetry.recipe_graphs import RecipeGraphProjectionError
from workbench_profile_supersymmetry.finite_item_matching import (
    ARTIFACT_SHA256, CLASS_SHA256, circuit_configuration,
)
from workbench_crucible_runtime_snapshot.capture import RuntimeCaptureError
import test_recipe_graph_projection as fixtures
from test_recipe_graph_projection import digest, item, recipe, selector


def nbt(**entries):
    return {"tag_id": 10, "value": entries}


def tag(kind, value):
    result = {"tag_id": kind, "value": value}
    if kind == 9:
        result['element_type'] = value[0]['tag_id'] if value else 0
    return result


def number(kind, value, decimal):
    code = 'f' if kind == 5 else 'd'
    raw = struct.pack('>' + code, value)
    return tag(kind, {"decimal": decimal, "raw_bits": str(int.from_bytes(raw, 'big')),
                      "value_kind": 'float32' if kind == 5 else 'float64'})


def chip(value=None, *, count=1):
    return dict(registry_name='fixture:chip', metadata=42, item_damage=42, count=count, tag=value)


class QualifiedFiniteMatchingTests(unittest.TestCase):
    setUp = fixtures.FiniteRecipeProjectionTests.setUp
    add_recipe = fixtures.FiniteRecipeProjectionTests.add_recipe
    publish = fixtures.FiniteRecipeProjectionTests.publish
    project = fixtures.FiniteRecipeProjectionTests.project
    rows = fixtures.FiniteRecipeProjectionTests.rows

    def rebind(self, header, witnesses):
        row = self.categories['gt-recipes'][1][0]
        row['semantic_sha256'] = digest(row['recipe'])
        pointers = ['/records/0/recipe/item_inputs/0/item_stack_representatives/0',
                    '/records/0/recipe/item_inputs/1/item_stack_representatives/0']
        pointers += [f'/records/0/recipe/item_outputs/{i}/value' for i in range(10)]
        pointers += ['/records/0/recipe/chanced_item_outputs/entries/0/value']
        header['domain_sha256'] = digest([dict(recipe_record_sha256=digest(row), pointer=p) for p in pointers])
        for witness in witnesses:
            witness.update(semantic_sha256=row['semantic_sha256'], recipe_record_sha256=digest(row))

    def prepare(self):
        value = recipe()
        ore = selector(stack=item(7, metadata=32767), amount=7)
        ore.update(runtime_class='gregtech.api.recipes.ingredients.GTRecipeOreInput',
                   ore_dictionary=True, ore_dictionary_id=2, ore_dictionary_name='oreFixture')
        circuit = selector(stack=chip(nbt(Configuration=tag(3, 1))), ordinal=1, reusable=True)
        circuit['runtime_class'] = 'gregtech.api.recipes.ingredients.IntCircuitIngredient'
        value['item_inputs'] = [ore, circuit]
        outputs = [item(tag=nbt(batch=tag(8, 'a')), metadata=9), item(64, metadata=9),
                   dict(item(metadata=9), registry_name='fixture:other'),
                   chip(nbt(Configuration=tag(3, 1), extra=tag(8, 'retained'))),
                   chip(nbt(Configuration=tag(3, 2))), chip(),
                   chip(nbt(Configuration=tag(8, '1'))),
                   chip(nbt(Configuration=number(5, 1.9, '1.9'))),
                   chip(nbt(Configuration=number(6, -0.1, '-0.1'))),
                   chip(nbt(Configuration=tag(4, 4294967297)))]
        value['item_outputs'] = [dict(ordinal=i, value=stack) for i, stack in enumerate(outputs)]
        value['chanced_item_outputs']['entries'][0]['value'] = item(metadata=15)
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)
        record = self.categories['gt-recipes'][1][0]
        record_hash = digest(record)
        # Independent explicit first-occurrence domain: two inputs, ten normal
        # outputs, one chance output. None has an identical count-free identity.
        pointers = ['/records/0/recipe/item_inputs/0/item_stack_representatives/0',
                    '/records/0/recipe/item_inputs/1/item_stack_representatives/0']
        pointers += [f'/records/0/recipe/item_outputs/{i}/value' for i in range(10)]
        pointers += ['/records/0/recipe/chanced_item_outputs/entries/0/value']
        header = dict(record_type='gt-item-matching-domain',
                      model='gt-2.8.10-forge-1.12.2-finite-item-matching-v1',
                      domain_scope='captured-gt-input-output-item-variants', domain_count=13,
                      domain_sha256=digest([dict(recipe_record_sha256=record_hash, pointer=p) for p in pointers]),
                      artifacts=dict(ARTIFACT_SHA256), class_sha256=dict(CLASS_SHA256))
        binding = dict(recipe_map='fixture_map', semantic_sha256=record['semantic_sha256'],
                       duplicate_ordinal=0, recipe_record_sha256=record_hash)
        witnesses = [
            dict(record_type='gt-item-matching-selector', **binding, selector_ordinal=0,
                 matcher='ore-dictionary', matching_configurations=None, integrated_circuit=None,
                 accepted_domain_ordinals=[0, 2, 3, 12]),
            dict(record_type='gt-item-matching-selector', **binding, selector_ordinal=1,
                 matcher='integrated-circuit', matching_configurations=1,
                 integrated_circuit={'registry_name': 'fixture:chip', 'item_damage': 42},
                 accepted_domain_ordinals=[1, 5, 9, 11]),
        ]
        self.categories['gt-item-matching'] = ('transformation-item-matching', [header, *witnesses])
        return value, header, witnesses

    def test_native_ore_and_configuration_closures_preserve_exact_resources_and_input(self):
        _, header, _ = self.prepare()
        self.publish()
        before = {p: p.read_bytes() for p in (*self.capture.iterdir(), self.inputs)}
        self.project()
        manifest, nodes, edges = self.rows()
        selectors = {n['properties']['ordinal']: n for n in nodes
                     if n['kind'] == 'gt-recipe-input-selector'
                     and n['properties']['runtime_class'].endswith(('GTRecipeOreInput', 'IntCircuitIngredient'))}
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(13, len([n for n in nodes if n['kind'] == 'item-variant']))
        for slot, expected in ((0, [0, 2, 3, 12]), (1, [1, 5, 9, 11])):
            node = selectors[slot]
            self.assertTrue(node['properties']['acceptance_complete'])
            self.assertEqual(header['domain_sha256'], node['properties']['acceptance_domain']['sha256'])
            self.assertEqual(1, len(node['properties']['captured_item_stack_representatives']))
            accepted = [e for e in edges if e['source'] == node['id']]
            self.assertEqual(expected, sorted(e['properties']['domain_ordinal'] for e in accepted))
            self.assertEqual({'accepts-gt-item-alternative'}, {e['relation'] for e in accepted})
            self.assertTrue(all(len(e['evidence']) == 2 for e in accepted))
            self.assertTrue(all(e['properties']['non_consumable'] == (slot == 1) for e in accepted))
        selected_recipe = next(n for n in nodes if n['kind'] == 'gt-recipe')
        self.assertEqual({'item': 2, 'fluid': 1}, selected_recipe['properties']['captured_input_counts'])
        self.assertEqual({'recipe_graphs.py', 'finite_item_matching.py', 'finite_ordinary_item_matching.py', 'item_names.py'}, set(manifest['evidence_binding']['projection_sources']))

    def test_missing_optional_category_keeps_ore_and_circuit_unknown(self):
        self.prepare()
        del self.categories['gt-item-matching']
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selectors = [n for n in nodes if n['kind'] == 'gt-recipe-input-selector'
                     and n['properties']['runtime_class'].endswith(('GTRecipeOreInput', 'IntCircuitIngredient'))]
        self.assertTrue(all(not n['properties']['acceptance_complete'] for n in selectors))
        self.assertEqual(2, len([e for e in edges if e['relation'] == 'observes-gt-item-input-representative']))

    def test_missing_individual_witness_does_not_promote_selector(self):
        self.prepare()
        self.categories['gt-item-matching'][1].pop()
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        circuit = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('IntCircuitIngredient'))
        self.assertFalse(circuit['properties']['acceptance_complete'])
        self.assertEqual(['observes-gt-item-input-representative'], [e['relation'] for e in edges if e['source'] == circuit['id']])

    def test_complete_empty_ore_domain_result_is_not_missing_evidence(self):
        value = recipe()
        value['item_inputs'] = [selector()]
        value['item_inputs'][0].update(runtime_class='gregtech.api.recipes.ingredients.GTRecipeOreInput',
                                      item_stack_representatives=[], ore_dictionary=True,
                                      ore_dictionary_id=2, ore_dictionary_name='oreEmpty')
        value['item_outputs'] = []
        value['chanced_item_outputs']['entries'] = []
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)
        row = self.categories['gt-recipes'][1][0]
        header = dict(record_type='gt-item-matching-domain', model='gt-2.8.10-forge-1.12.2-finite-item-matching-v1',
                      domain_scope='captured-gt-input-output-item-variants', domain_sha256=digest([]), domain_count=0,
                      artifacts=dict(ARTIFACT_SHA256), class_sha256=dict(CLASS_SHA256))
        witness = dict(record_type='gt-item-matching-selector', recipe_map='fixture_map',
                       semantic_sha256=row['semantic_sha256'], duplicate_ordinal=0, recipe_record_sha256=digest(row),
                       selector_ordinal=0, matcher='ore-dictionary', matching_configurations=None,
                       integrated_circuit=None, accepted_domain_ordinals=[])
        self.categories['gt-item-matching'] = ('transformation-item-matching', [header, witness])
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        ore = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('GTRecipeOreInput'))
        self.assertTrue(ore['properties']['acceptance_complete'])
        self.assertEqual(0, ore['properties']['acceptance_domain']['count'])
        self.assertFalse(any(e['source'] == ore['id'] for e in edges))

    def test_resealed_disagreement_and_invalid_native_bindings_refuse(self):
        mutations = [
            lambda h, w: w[0]['accepted_domain_ordinals'].remove(2),
            lambda h, w: w[0]['accepted_domain_ordinals'].append(4),
            lambda h, w: w[1].update(matching_configurations=2),
            lambda h, w: w[0].update(recipe_record_sha256='f' * 64),
            lambda h, w: w[0].update(selector_ordinal=True),
            lambda h, w: w[0].update(duplicate_ordinal=True),
            lambda h, w: w[0].update(accepted_domain_ordinals=[True]),
            lambda h, w: w[0].update(accepted_domain_ordinals=[0, 0]),
            lambda h, w: w[0].update(accepted_domain_ordinals=[99]),
            lambda h, w: h.update(domain_count=12),
            lambda h, w: h.update(domain_count=True),
            lambda h, w: h.update(domain_sha256='f' * 64),
            lambda h, w: h['artifacts'].update(forge_sha256='f' * 64),
            lambda h, w: h['class_sha256'].update(GTRecipeOreInput='f' * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                _, header, witnesses = self.prepare()
                mutate(header, witnesses)
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError):
                    self.project()
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.home.glob('.atlas-recipe-projection-*')))

    def test_partial_optional_category_refuses_even_when_recipes_complete(self):
        self.prepare()
        self.publish(category_changes={'gt-item-matching': {'stable': False}})
        with self.assertRaises(RuntimeCaptureError):
            self.project()

    def test_custom_matcher_cannot_be_native_promoted(self):
        value, header, witnesses = self.prepare()
        value['item_inputs'][0]['nbt_matcher'] = 'custom'
        self.rebind(header, witnesses)
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'custom NBT matching'):
            self.project()

    def test_long_array_and_list_of_long_arrays_do_not_change_configuration(self):
        value, header, witnesses = self.prepare()
        value['item_outputs'][3]['value']['tag']['value'].update(
            long_array=tag(12, [-9223372036854775808, 0, 9223372036854775807]),
            nested_arrays=tag(9, [tag(12, []), tag(12, [123])]))
        self.rebind(header, witnesses)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        target = next(n for n in nodes if n['kind'] == 'item-variant'
                      and n['properties']['tag'] and 'long_array' in n['properties']['tag']['value'])
        self.assertEqual([-9223372036854775808, 0, 9223372036854775807],
                         target['properties']['tag']['value']['long_array']['value'])
        self.assertTrue(any(e['target'] == target['id'] and e['relation'] == 'accepts-gt-item-alternative' for e in edges))

    def test_ore_uses_metadata_and_target_only_wildcard_while_circuit_uses_damage(self):
        value, header, witnesses = self.prepare()
        # Forge compares getMetadata. A wildcard only in getItemDamage does
        # not broaden matching; a candidate metadata wildcard is not symmetric.
        ore_stack = value['item_inputs'][0]['item_stack_representatives'][0]
        ore_stack.update(metadata=9, item_damage=32767)
        value['item_outputs'][0]['value']['item_damage'] = 5
        value['chanced_item_outputs']['entries'][0]['value'].update(metadata=32767, item_damage=9)
        # MetaValueItem compares getItemDamage, ignoring this metadata change.
        value['item_outputs'][3]['value']['metadata'] = 999
        witnesses[0]['accepted_domain_ordinals'] = [0, 2, 3]
        self.rebind(header, witnesses)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        ore = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('GTRecipeOreInput'))
        circuit = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('IntCircuitIngredient'))
        self.assertEqual([0, 2, 3], sorted(e['properties']['domain_ordinal'] for e in edges if e['source'] == ore['id']))
        self.assertEqual([1, 5, 9, 11], sorted(e['properties']['domain_ordinal'] for e in edges if e['source'] == circuit['id']))

    def test_malformed_typed_nbt_refuses_instead_of_fabricating_domain(self):
        bad_tags = [tag(12, [9223372036854775808]), tag(12, [True]),
                    tag(9, [tag(12, []), tag(3, 1)]), tag(13, []),
                    tag(5, dict(decimal='1.0', raw_bits='0', value_kind='float32')),
                    {'tag_id': 9, 'value': []}, {'tag_id': 9, 'value': [], 'element_type': True},
                    {'tag_id': 9, 'value': [], 'element_type': 13},
                    {'tag_id': 9, 'value': [tag(3, 1)], 'element_type': 0}]
        for invalid in bad_tags:
            with self.subTest(invalid=invalid):
                value, header, witnesses = self.prepare()
                value['item_outputs'][3]['value']['tag']['value']['extra'] = invalid
                self.rebind(header, witnesses)
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'NBT'):
                    self.project()

    def test_empty_list_stored_element_type_preserves_distinct_resource_identity(self):
        value, header, witnesses = self.prepare()
        value['item_outputs'][0]['value']['tag'] = nbt(empty=tag(9, []))
        value['item_outputs'][1]['value']['tag'] = nbt(empty={'tag_id': 9, 'value': [], 'element_type': 3})
        self.rebind(header, witnesses)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        typed_empty = [n for n in nodes if n['kind'] == 'item-variant'
                       and n['properties']['tag'] and 'empty' in n['properties']['tag']['value']]
        self.assertEqual(2, len(typed_empty))
        self.assertEqual({0, 3}, {n['properties']['tag']['value']['empty']['element_type'] for n in typed_empty})
        self.assertEqual(2, len({n['id'] for n in typed_empty}))
        accepted_ids = {e['target'] for e in edges if e['relation'] == 'accepts-gt-item-alternative'}
        self.assertTrue({n['id'] for n in typed_empty}.issubset(accepted_ids))

    def test_quantity_duplicates_do_not_extend_domain_and_preserve_first_locator(self):
        value, header, witnesses = self.prepare()
        duplicate = deepcopy(value['item_outputs'][0]['value'])
        duplicate['count'] = 128
        value['item_outputs'].append({'ordinal': 10, 'value': duplicate})
        self.rebind(header, witnesses)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        self.assertEqual(13, len([n for n in nodes if n['kind'] == 'item-variant']))
        accepted = next(e for e in edges if e['relation'] == 'accepts-gt-item-alternative'
                        and e['properties']['domain_ordinal'] == 2)
        self.assertEqual(1, accepted['properties']['observed_stack']['count'])
        self.assertTrue(any(e['pointer'] == '/records/0/recipe/item_outputs/0/value' for e in accepted['evidence']))
        self.assertTrue(any(e['relation'] == 'produces-gt-item' and e['properties']['amount'] == 128 for e in edges))

    def test_cancellation_during_matching_keeps_inputs_and_does_not_publish(self):
        self.prepare()
        self.publish()
        before = {p: p.read_bytes() for p in (*self.capture.iterdir(), self.inputs)}
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 30:
                raise InterruptedError('fixture cancellation')
        with self.assertRaises(InterruptedError):
            project_capture(self.capture, self.output, input_manifest=self.inputs, check_cancelled=cancel)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.home.glob('.atlas-recipe-projection-*')))

    def test_duplicate_witness_and_domain_header_refuse(self):
        for ordinal in (0, 1):
            with self.subTest(record=ordinal):
                self.prepare()
                records = self.categories['gt-item-matching'][1]
                records.append(deepcopy(records[ordinal]))
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'duplicated|exactly one'):
                    self.project()

    def branch_inputs(self):
        self.input_value.update(
            format='workbench-supersymmetry-branch-observation-input-v1',
            pack_source={'revision': '575b540c64f2d2e33222660da1a78694bcae3eb9',
                         'tree': '7bda9fceffeee614f7c694c0d908ec76e72e34f8'},
            platform={'minecraft_version': '1.12.2', 'loader': 'forge', 'loader_version': '14.23.5.2860', 'java_major': 8},
            runtime_artifacts={**ARTIFACT_SHA256,
                'groovyscript_sha256': '07617b7ce9170a857199bd61d730a0db3af2685cf83d31734a2d4b628fda7533',
                'susy_core_sha256': 'ec9b56070f8788b63f5d381c765cd05ed66ba42755e1f87ad2eb6605285a5a46'},
            candidate_lock_sha256='c' * 64, adapter_profile_sha256='d' * 64)
        self.input_value['pack_binding_id'] = 'supersymmetry-branch-observation:sha256:' + digest(self.input_value['pack_source'])
        self.input_value['platform_binding_id'] = 'forge-branch-observation:sha256:' + digest({
            key: self.input_value[key] for key in ('platform', 'runtime_artifacts')})

    def test_explicit_original_forge_branch_preserves_same_checkpoint(self):
        self.prepare()
        self.branch_inputs()
        self.publish()
        self.project()
        manifest, _, _ = self.rows()
        self.assertEqual('post-start-end-tick', manifest['scope']['lifecycle_checkpoint_id'])
        self.assertEqual(self.input_value, manifest['evidence_binding']['input_manifest'])

    def test_branch_pin_hash_protocol_and_earlier_checkpoint_refuse(self):
        mutations = [lambda v: v['pack_source'].update(revision='f' * 40),
                     lambda v: v['platform'].update(java_major=25),
                     lambda v: v['platform'].update(java_major=8.0),
                     lambda v: v['runtime_artifacts'].update(susy_core_sha256='f' * 64),
                     lambda v: v.update(candidate_lock_sha256='f' * 64),
                     lambda v: v.update(platform_binding_id='forge-branch-observation:sha256:' + 'f' * 64)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.prepare()
                self.branch_inputs()
                mutate(self.input_value)
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError):
                    self.project()
        self.branch_inputs()
        self.publish(category_changes={'gt-recipes': {'checkpoint_id': 'load-complete'}})
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'checkpoint'):
            self.project()

    def test_configuration_numeric_java_semantics(self):
        cases = [
            (None, 0), (nbt(), 0), (nbt(Configuration=tag(8, '1')), 0),
            (nbt(Configuration=tag(1, -1)), -1), (nbt(Configuration=tag(2, 32)), 32),
            (nbt(Configuration=tag(4, 4294967297)), 1),
            (nbt(Configuration=number(5, -0.1, '-0.1')), -1),
            (nbt(Configuration=number(6, 1.9, '1.9')), 1),
            (nbt(Configuration=number(5, float('nan'), 'NaN')), 0),
            (nbt(Configuration=number(6, float('-inf'), '-Infinity')), 2147483647),
            (nbt(Configuration=number(5, 2147483648.0, '2.14748365E9')), 2147483647),
            (nbt(Configuration=tag(3, 1), other=tag(8, 'allowed')), 1),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                original = deepcopy(value)
                self.assertEqual(expected, circuit_configuration(value))
                self.assertEqual(original, value)


if __name__ == '__main__':
    unittest.main()
