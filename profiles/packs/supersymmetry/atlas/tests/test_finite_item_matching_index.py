"""Indexed prediction agrees with independently enumerated literal domains."""
from copy import deepcopy
import unittest

from workbench_profile_supersymmetry.finite_item_matching import (
    ARTIFACT_SHA256, CLASS_SHA256, DOMAIN_SCOPE, MATCHING_MODEL,
    FiniteItemMatchingError, qualify_item_matching,
)
from test_finite_item_matching import chip, nbt, number, tag
from test_recipe_graph_projection import digest, item, recipe, selector


def fixture():
    # Metadata and item damage intentionally disagree. A wildcard candidate
    # alone must not broaden an exact target, while a wildcard target must.
    domain = [
        dict(item(metadata=9), item_damage=32767),
        dict(item(metadata=32767), item_damage=9),
        dict(item(metadata=9, tag=nbt(extra=tag(8, 'x'))), item_damage=5),
        dict(item(metadata=15), item_damage=9),
        dict(item(metadata=9), registry_name='fixture:other'),
        chip(), chip(nbt()), chip(nbt(Configuration=tag(3, 1))),
        chip(nbt(Configuration=tag(3, 2))), chip(nbt(Configuration=tag(8, '1'))),
        chip(nbt(Configuration=tag(1, -1))),
        chip(nbt(Configuration=tag(4, 4294967297))),
        chip(nbt(Configuration=number(5, 1.9, '1.9'))),
        chip(nbt(Configuration=number(6, -0.1, '-0.1'))),
        dict(chip(nbt(Configuration=tag(3, 1), extra=tag(8, 'x'))), metadata=999),
        dict(chip(nbt(Configuration=tag(3, 1))), item_damage=99),
    ]
    domain += [dict(item(metadata=i), registry_name=f'fixture:unrelated_{i}') for i in range(256)]
    # These are literal expected original getInteger results, independent of
    # the implementation's circuit_configuration function and its indexes.
    configurations = {5: 0, 6: 0, 7: 1, 8: 2, 9: 0, 10: -1,
                      11: 1, 12: 1, 13: -1, 14: 1, 15: 1}
    selectors, expectations, models = [], [], []
    for target_ordinals in ([0], [1], [0, 1, 0], [4], [], [0]):
        targets = [domain[index] for index in target_ordinals]
        current = selector(ordinal=len(selectors))
        current.update(runtime_class='gregtech.api.recipes.ingredients.GTRecipeOreInput',
                       ore_dictionary=True, ore_dictionary_id=1, ore_dictionary_name='fixtureOre',
                       item_stack_representatives=deepcopy(targets))
        selectors.append(current)
        expectations.append([index for index, candidate in enumerate(domain)
                             if any(target['registry_name'] == candidate['registry_name']
                                    and (target['metadata'] == 32767 or target['metadata'] == candidate['metadata'])
                                    for target in targets)])
        models.append(('ore-dictionary', None))
    for config in (0, 1, 2, -1, 3, 1):
        current = selector(ordinal=len(selectors))
        current.update(runtime_class='gregtech.api.recipes.ingredients.IntCircuitIngredient',
                       item_stack_representatives=[])
        selectors.append(current)
        expectations.append([index for index, candidate in enumerate(domain)
                             if candidate['registry_name'] == 'fixture:chip' and candidate['item_damage'] == 42
                             and configurations[index] == config])
        models.append(('integrated-circuit', config))
    rows = []
    for inputs, outputs in (([], domain), (selectors, [])):
        value = recipe()
        value.update(item_inputs=inputs, item_outputs=[dict(ordinal=i, value=stack) for i, stack in enumerate(outputs)])
        value['chanced_item_outputs']['entries'] = []
        rows.append(dict(record_type='gt-recipe', recipe_map='fixture_map',
                         semantic_sha256=digest(value), duplicate_ordinal=0, lookup_active=True,
                         category_present=True, recipe=value))
    hashes = tuple(digest(row) for row in rows)
    locators = [dict(recipe_record_sha256=hashes[0], pointer=f'/records/0/recipe/item_outputs/{i}/value')
                for i in range(len(domain))]
    records = [dict(record_type='gt-item-matching-domain', model=MATCHING_MODEL,
                    domain_scope=DOMAIN_SCOPE, domain_sha256=digest(locators), domain_count=len(domain),
                    artifacts=dict(ARTIFACT_SHA256), class_sha256=dict(CLASS_SHA256))]
    for ordinal, ((matcher, config), expected) in enumerate(zip(models, expectations)):
        records.append(dict(record_type='gt-item-matching-selector', recipe_map='fixture_map',
                            semantic_sha256=rows[1]['semantic_sha256'], duplicate_ordinal=0,
                            recipe_record_sha256=hashes[1], selector_ordinal=ordinal, matcher=matcher,
                            matching_configurations=config,
                            integrated_circuit=None if config is None else dict(registry_name='fixture:chip', item_damage=42),
                            accepted_domain_ordinals=expected))
    return records, rows, hashes, domain, expectations


class FiniteMatchingIndexTests(unittest.TestCase):
    def test_mixed_domain_matches_independent_exhaustive_predicates(self):
        records, rows, hashes, domain, expected = fixture()
        original = deepcopy((records, rows))
        result = qualify_item_matching(records, rows, hashes, check_cancelled=lambda: None)
        self.assertEqual(tuple(domain), result.domain.stacks)
        self.assertEqual([tuple(value) for value in expected],
                         [result.selectors[1, slot].accepted_domain_ordinals for slot in range(len(expected))])
        self.assertEqual([0, 2], expected[0])
        self.assertEqual([0, 1, 2, 3], expected[1])
        self.assertEqual([7, 11, 12, 14], expected[7])
        self.assertEqual([], expected[10])
        self.assertEqual(original, (records, rows))

    def test_native_extra_missing_and_reordered_alternatives_still_refuse(self):
        for replacement in ([0, 1, 2], [0], [2, 0]):
            with self.subTest(replacement=replacement):
                records, rows, hashes, _, _ = fixture()
                records[1]['accepted_domain_ordinals'] = replacement
                with self.assertRaises(FiniteItemMatchingError):
                    qualify_item_matching(records, rows, hashes, check_cancelled=lambda: None)

    def test_duplicate_witness_still_refuses_after_indexing(self):
        records, rows, hashes, _, _ = fixture()
        records.append(deepcopy(records[1]))
        with self.assertRaisesRegex(FiniteItemMatchingError, 'duplicated'):
            qualify_item_matching(records, rows, hashes, check_cancelled=lambda: None)

    def test_cancellation_remains_available_through_domain_and_prediction(self):
        records, rows, hashes, _, _ = fixture()
        calls = 0
        def count():
            nonlocal calls
            calls += 1
        qualify_item_matching(records, rows, hashes, check_cancelled=count)
        # Exercise every cancellation checkpoint, including the single domain
        # index and the subsequent selector candidates, without relying on its
        # implementation-specific location or a wall-clock timing threshold.
        for stop in range(1, calls + 1):
            current = 0
            def cancel():
                nonlocal current
                current += 1
                if current == stop:
                    raise InterruptedError('fixture cancellation')
            with self.subTest(checkpoint=stop), self.assertRaises(InterruptedError):
                qualify_item_matching(records, rows, hashes, check_cancelled=cancel)


if __name__ == '__main__':
    unittest.main()
