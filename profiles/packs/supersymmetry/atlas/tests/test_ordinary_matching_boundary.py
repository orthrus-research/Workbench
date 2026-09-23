"""Counterexamples to treating stored representatives as matcher closure."""
import unittest

import test_recipe_graph_projection as fixtures
from test_recipe_graph_projection import fluid, item, recipe, selector


def compound(**fields):
    return {'tag_id': 10, 'value': fields}


def floating(kind, decimal, bits):
    return {'tag_id': kind, 'value': {'decimal': decimal, 'raw_bits': str(bits),
                                     'value_kind': 'float32' if kind == 5 else 'float64'}}


class OrdinaryMatchingBoundaryTests(unittest.TestCase):
    setUp = fixtures.FiniteRecipeProjectionTests.setUp
    add_recipe = fixtures.FiniteRecipeProjectionTests.add_recipe
    publish = fixtures.FiniteRecipeProjectionTests.publish
    project = fixtures.FiniteRecipeProjectionTests.project
    rows = fixtures.FiniteRecipeProjectionTests.rows

    def replace_recipe(self, value):
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)

    def test_ordinary_metadata_and_damage_counterexample_never_claims_closure(self):
        # Original GT compares getMetadata, while retained resource identity
        # also includes item damage. Capabilities are not retained at all.
        value = recipe()
        representative = dict(item(metadata=0), item_damage=1)
        other_damage = dict(item(metadata=0), item_damage=7)
        value['item_inputs'] = [selector(stack=representative, amount=1)]
        value['item_outputs'] = [{'ordinal': 0, 'value': other_damage}]
        value['chanced_item_outputs']['entries'] = []
        self.replace_recipe(value)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selected = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('.GTRecipeItemInput'))
        self.assertFalse(selected['properties']['acceptance_complete'])
        self.assertIn('ordinary-item-matching-closure-not-qualified', selected['properties']['acceptance_gaps'])
        self.assertEqual(['item-capability-compatibility-not-captured'], selected['properties']['matching_limits'])
        self.assertEqual([representative], selected['properties']['captured_item_stack_representatives'])
        outgoing = [e for e in edges if e['source'] == selected['id']]
        self.assertEqual(['observes-gt-item-input-representative'], [e['relation'] for e in outgoing])
        resources = [n for n in nodes if n['kind'] == 'item-variant']
        self.assertEqual({1, 7}, {n['properties']['item_damage'] for n in resources})

    def test_ordinary_exact_nbt_and_reusable_roles_remain_observed(self):
        value = recipe()
        value['item_inputs'] = [selector(stack=item(tag=compound(batch={'tag_id': 3, 'value': 1})),
                                                 amount=4, reusable=True)]
        self.replace_recipe(value)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selected = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('.GTRecipeItemInput'))
        self.assertFalse(selected['properties']['acceptance_complete'])
        observed = [e for e in edges if e['source'] == selected['id']]
        self.assertEqual(['observes-gt-item-input-representative'], [e['relation'] for e in observed])
        self.assertEqual((4, True), (observed[0]['properties']['amount'], observed[0]['properties']['non_consumable']))

    def test_float_nbt_fluid_equality_cannot_be_replaced_by_exact_identity(self):
        # Original NBT float/double equality equates signed zero; NaN may
        # fail equality even against a copied tag with identical raw bits.
        value = recipe()
        value['item_inputs'] = []
        plus = compound(nested={'tag_id': 9, 'element_type': 5,
                                'value': [floating(5, '0.0', 0)]})
        minus = compound(nested={'tag_id': 9, 'element_type': 5,
                                 'value': [floating(5, '-0.0', 2147483648)]})
        nan = compound(nested={'tag_id': 10, 'value': {'number': floating(6, 'NaN', 9221120237041090560)}})
        value['fluid_inputs'] = [selector(fluid_stack=fluid(tag=plus)),
                                 selector(fluid_stack=fluid(tag=nan), ordinal=1)]
        value['fluid_outputs'] = [{'ordinal': 0, 'value': fluid(tag=minus)}]
        value['chanced_fluid_outputs']['entries'] = []
        self.replace_recipe(value)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selected = [n for n in nodes if n['properties'].get('runtime_class', '').endswith('.GTRecipeFluidInput')]
        self.assertEqual(2, len(selected))
        for node in selected:
            self.assertFalse(node['properties']['acceptance_complete'])
            self.assertEqual(['floating-nbt-equality-not-qualified'], node['properties']['acceptance_gaps'])
            self.assertEqual(['observes-gt-fluid-input-representative'],
                             [e['relation'] for e in edges if e['source'] == node['id']])
        self.assertEqual(3, len([n for n in nodes if n['kind'] == 'forge-fluid']))

    def test_nonfloating_default_fluid_equality_keeps_exact_edge(self):
        value = recipe()
        tag = compound(batch={'tag_id': 3, 'value': 7}, label={'tag_id': 8, 'value': 'exact'})
        value['fluid_inputs'] = [selector(fluid_stack=fluid(tag=tag))]
        self.replace_recipe(value)
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selected = next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('.GTRecipeFluidInput'))
        self.assertTrue(selected['properties']['acceptance_complete'])
        self.assertEqual([], selected['properties']['acceptance_gaps'])
        self.assertEqual(['accepts-gt-fluid-input'], [e['relation'] for e in edges if e['source'] == selected['id']])


if __name__ == '__main__':
    unittest.main()
