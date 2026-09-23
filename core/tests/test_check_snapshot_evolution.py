"""Pack evolution fixtures prove reader/storage meaning, not native support."""

from copy import deepcopy
import unittest

from test_check_snapshot_contract import fixture, digest
from workbench_core import check_snapshot_contract as contract
from workbench_core import check_snapshot_evolution as evolution


class SnapshotEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.body, self.scope = fixture()
        self.before = contract.seal_snapshot(self.body, self.scope)
        self.schemas = {'fixture-recipes-v1', 'fixture-values-v1'}

    def comparison(self):
        self.body['scope_id'] = contract.scope_identity(self.scope)
        after = contract.seal_snapshot(self.body, self.scope)
        return evolution.compare(self.before, after, self.schemas)

    def test_refactor_and_pack_binding_changes_do_not_change_observation_contracts(self):
        self.body['producer']['build_sha256'] = digest('refactor')
        self.body['bindings']['context'] = digest('new pack')
        result = self.comparison()
        self.assertEqual('unchanged', result['state'])
        self.assertTrue(result['producer_changed'])
        self.assertEqual(['context'], result['changed_bindings'])
        self.assertFalse(result['native_support_established'])

    def test_same_parent_digest_with_changed_child_cannot_claim_unchanged(self):
        self.body['sections'][1]['content']['sha256'] = digest('different child')
        result = self.comparison()
        self.assertEqual('changed', result['state'])
        self.assertEqual(['values'], result['comparable'][0]['changed_content_sections'])

    def test_unknown_child_blocks_required_parent_interpretation_and_comparison(self):
        self.body['sections'][1]['schema'] = 'unknown-v2'
        after = contract.seal_snapshot(self.body, self.scope)
        result = evolution.interpretation(after, self.schemas)
        self.assertEqual(['recipes', 'values'], result['affected_sections'])
        self.assertFalse(result['required_interpretation_complete'])
        self.assertEqual('complete', after['coverage'])
        self.assertEqual(2, len(self.comparison()['incompatible']))

    def test_unknown_optional_section_does_not_block_unrelated_required_evidence(self):
        section = deepcopy(self.body['sections'][1])
        section.update(id='optional', schema='future-v5', required=False, dependencies=[])
        self.scope['sections']['optional'] = False
        self.body['sections'].insert(0, section)
        self.body['scope_id'] = contract.scope_identity(self.scope)
        after = contract.seal_snapshot(self.body, self.scope)
        self.assertTrue(evolution.interpretation(after, self.schemas)['required_interpretation_complete'])
        self.assertEqual([{'section': 'optional', 'change': 'added-section'}], self.comparison()['scope_changes'])

    def test_required_optional_and_removed_scope_are_not_observer_failure(self):
        self.scope['sections']['recipes'] = False
        self.body['sections'][0]['required'] = False
        self.assertEqual('requirement-changed', self.comparison()['scope_changes'][0]['change'])
        self.body['sections'][0].update(state='not-applicable', count=None, content=None,
                                       dependencies=[], reason='selected profile no longer contains this feature')
        self.assertEqual('scope-changed', self.comparison()['state'])
        self.body['sections'].pop(0)
        del self.scope['sections']['recipes']
        self.assertEqual('removed-section', self.comparison()['scope_changes'][0]['change'])

    def test_missing_observation_is_incompatible_not_removed_or_empty(self):
        self.body['sections'][0].update(state='unavailable', count=None, content=None,
                                       dependencies=[], reason='observer failed')
        self.body['coverage'] = 'incomplete'
        result = self.comparison()
        self.assertEqual([], result['scope_changes'])
        self.assertEqual('incompatible', result['state'])

    def test_split_and_merge_require_explicit_mapping_and_keep_scope_changes_visible(self):
        self.body['sections'][0]['id'] = 'recipes-a'
        extra = deepcopy(self.body['sections'][0]); extra['id'] = 'recipes-b'
        self.body['sections'].insert(1, extra)
        del self.scope['sections']['recipes']
        self.scope['sections'].update({'recipes-a': True, 'recipes-b': True})
        split = self.comparison()
        self.assertEqual(['removed-section', 'added-section', 'added-section'],
                         [row['change'] for row in split['scope_changes']])
        after = contract.seal_snapshot(self.body, self.scope)
        merged = evolution.compare(after, self.before, self.schemas)
        self.assertEqual(['added-section', 'removed-section', 'removed-section'],
                         [row['change'] for row in merged['scope_changes']])
        self.assertFalse(merged['native_support_established'])

    def test_unknown_scope_never_certifies_a_changed_applicable_scope(self):
        result = evolution.compare(self.before, self.before, self.schemas, before_scope=False)
        self.assertEqual('incompatible', result['state'])
        self.assertEqual([], result['comparable'])

    def test_dependency_cycles_terminate_and_keep_changed_descendants_visible(self):
        self.body['sections'][1]['dependencies'] = ['recipes']
        self.before = contract.seal_snapshot(self.body, self.scope)
        self.body['sections'][0]['content']['sha256'] = digest('cycle change')
        self.assertTrue(all(row['state'] == 'changed' for row in self.comparison()['comparable']))

    def test_derived_reader_preserves_order_duplicates_types_and_refuses_missing_fields(self):
        source = {'rows': [True, 1, 1.0, None, '1', '1']}
        original = deepcopy(source)
        fields = dict(snapshot_id=self.before['id'], input_contract='fixture-old-v1', output_contract='fixture-new-v1',
                      converter='fixture-field-mapping-v1', converter_sha256=digest('converter'))
        output, binding = evolution.derived_view(source, **fields, convert=lambda value: {'records': value['rows']})
        self.assertEqual([bool, int, float, type(None), str, str], [type(v) for v in output['records']])
        self.assertEqual(original, source)
        with self.assertRaises(KeyError):
            evolution.derived_view({}, **fields, convert=lambda value: {'records': value['rows']})
        _, newer = evolution.derived_view(source, **{**fields, 'converter_sha256': digest('refactored converter')},
                                         convert=lambda value: {'records': value['rows']})
        self.assertNotEqual(binding['id'], newer['id'])
        self.assertEqual(binding['output_sha256'], newer['output_sha256'])
        with self.assertRaisesRegex(ValueError, 'mutated'):
            evolution.derived_view(source, **fields, convert=lambda value: value.pop('rows'))
        self.assertEqual(original, source)


if __name__ == '__main__':
    unittest.main()
