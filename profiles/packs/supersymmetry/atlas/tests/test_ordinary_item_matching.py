"""Literal raw-occurrence coverage and original-state ordinary matcher cases."""
from copy import deepcopy
import unittest

import test_recipe_graph_projection as fixtures
from test_recipe_graph_projection import digest, item, recipe, selector
from workbench_profile_supersymmetry.recipe_graphs import RecipeGraphProjectionError
from workbench_profile_supersymmetry.finite_ordinary_item_matching import (
    NATIVE_COPY_INITIALIZATION_POLICY, ORDINARY_ARTIFACT_SHA256, ORDINARY_CLASS_SHA256, ORDINARY_MODEL,
    qualify_ordinary_item_matching,
)


class OrdinaryItemMatchingTests(unittest.TestCase):
    setUp = fixtures.FiniteRecipeProjectionTests.setUp
    add_recipe = fixtures.FiniteRecipeProjectionTests.add_recipe
    publish = fixtures.FiniteRecipeProjectionTests.publish
    project = fixtures.FiniteRecipeProjectionTests.project
    rows = fixtures.FiniteRecipeProjectionTests.rows

    def prepare(self):
        value = recipe()
        value['item_inputs'] = [selector(stack=item(4, metadata=9), amount=4, reusable=True)]
        value['fluid_inputs'] = []
        first = dict(item(2, metadata=3), item_damage=11)
        second = dict(first, count=64)
        different_damage = dict(item(metadata=3), item_damage=12)
        tagged = item(tag={'tag_id': 10, 'value': {'batch': {'tag_id': 3, 'value': 7}}})
        other = dict(item(), registry_name='fixture:other')
        outputs = [first, second, different_damage, tagged, other]
        value['item_outputs'] = [dict(ordinal=i, value=stack) for i, stack in enumerate(outputs)]
        value['chanced_item_outputs']['entries'][0]['value'] = dict(first, count=8)
        self.categories['gt-recipes'][1].clear()
        self.add_recipe(value)
        record = self.categories['gt-recipes'][1][0]
        record_hash = digest(record)
        pointers = ['/records/0/recipe/item_inputs/0/item_stack_representatives/0']
        pointers += [f'/records/0/recipe/item_outputs/{i}/value' for i in range(5)]
        pointers += ['/records/0/recipe/chanced_item_outputs/entries/0/value']
        locators = [dict(recipe_record_sha256=record_hash, pointer=p) for p in pointers]
        stacks = [value['item_inputs'][0]['item_stack_representatives'][0], *outputs,
                  value['chanced_item_outputs']['entries'][0]['value']]
        # Independently specified grouping: repeated quantities2,64,8 collapse;
        # same metadata with item damage11 vs12 remains separate resources.
        groups = [[0], [1, 2, 6], [3], [4], [5]]
        header = dict(record_type='gt-ordinary-item-matching-domain', model=ORDINARY_MODEL,
                      native_copy_initialization_policy=NATIVE_COPY_INITIALIZATION_POLICY,
                      domain_scope='captured-gt-input-output-item-variants', domain_count=5,
                      domain_sha256=digest([locators[i] for i in (0, 1, 3, 4, 5)]),
                      occurrence_count=7, occurrences_sha256=digest(locators),
                      artifacts=dict(ORDINARY_ARTIFACT_SHA256), class_sha256=dict(ORDINARY_CLASS_SHA256))
        candidates = []
        for ordinal, group in enumerate(groups):
            candidates.append(dict(record_type='gt-ordinary-item-matching-candidate', domain_ordinal=ordinal,
                occurrence_count=len(group), occurrences_sha256=digest([locators[i] for i in group]),
                occurrences=[dict(**locators[i], copy_stack=deepcopy(stacks[i]),
                    original_initialized_stack=deepcopy(stacks[i]),
                    copy_before_initialization_stack=deepcopy(stacks[i]),
                    original_capability_initialization_state='initialized',
                    copy_capability_initialization_state='initialized',
                    original_capability_writer_count=0, copy_capability_writer_count=0) for i in group]))
        witness = dict(record_type='gt-ordinary-item-matching-selector', recipe_map='fixture_map',
                       semantic_sha256=record['semantic_sha256'], duplicate_ordinal=0,
                       recipe_record_sha256=record_hash, selector_ordinal=0,
                       targets=[dict(registry_name='fixture:tool', metadata=3, tag=None, capability_writer_count=0,
                                     stack_before_initialization=deepcopy(first), stack_after_initialization=deepcopy(first),
                                     capability_initialization_state='initialized')],
                       accepted_domain_ordinals=[1, 2], accepted_occurrence_ordinals=[1, 2, 3, 6])
        self.categories['gt-ordinary-item-matching'] = ('transformation-ordinary-item-matching', [header, *candidates, witness])
        return value, header, candidates, witness

    def ordinary_selector(self, nodes):
        return next(n for n in nodes if n['properties'].get('runtime_class', '').endswith('.GTRecipeItemInput'))

    def test_original_stored_metadata_and_every_copy_qualify_without_changing_resources(self):
        value, header, _, witness = self.prepare()
        self.publish()
        before = {p: p.read_bytes() for p in (*self.capture.iterdir(), self.inputs)}
        self.project()
        manifest, nodes, edges = self.rows()
        selected = self.ordinary_selector(nodes)
        self.assertTrue(selected['properties']['acceptance_complete'])
        self.assertEqual([], selected['properties']['acceptance_gaps'])
        self.assertEqual([], selected['properties']['matching_limits'])
        self.assertEqual(ORDINARY_MODEL, selected['properties']['acceptance_model'])
        self.assertEqual(witness['targets'], selected['properties']['matching_state']['targets'])
        self.assertEqual(value['item_inputs'][0]['item_stack_representatives'], selected['properties']['captured_item_stack_representatives'])
        self.assertEqual({'scope': header['domain_scope'], 'sha256': header['domain_sha256'], 'count': 5,
                          'occurrence_count': 7, 'occurrences_sha256': header['occurrences_sha256']}, selected['properties']['acceptance_domain'])
        accepted = [e for e in edges if e['source'] == selected['id']]
        self.assertEqual({'accepts-gt-item-alternative'}, {e['relation'] for e in accepted})
        self.assertEqual([1, 2], sorted(e['properties']['domain_ordinal'] for e in accepted))
        resources = {n['id']: n for n in nodes if n['kind'] == 'item-variant'}
        self.assertEqual({11, 12}, {resources[e['target']]['properties']['item_damage'] for e in accepted})
        self.assertTrue(all(e['properties']['amount'] == 4 and e['properties']['non_consumable'] for e in accepted))
        self.assertTrue(all(e['evidence'][0]['adapter_id'] == 'gt-ordinary-item-matching' for e in accepted))
        self.assertEqual(1, manifest['evidence_binding']['ordinary_item_matching']['qualified_selector_count'])
        self.assertTrue(manifest['evidence_binding']['ordinary_item_matching']['capability_initialization_complete'])
        self.assertTrue(manifest['evidence_binding']['ordinary_item_matching']['initialization_identities_unchanged'])
        self.assertTrue(manifest['evidence_binding']['ordinary_item_matching']['initialization_counts_unchanged'])
        partition = next(p for p in manifest['partitions'] if p['partition_id'] == 'recipes')
        self.assertIn('gt-ordinary-item-matching', partition['evidence_categories'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_missing_witness_keeps_ordinary_matching_unknown(self):
        self.prepare()
        self.categories['gt-ordinary-item-matching'][1].pop()
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selected = self.ordinary_selector(nodes)
        self.assertFalse(selected['properties']['acceptance_complete'])
        self.assertEqual(['observes-gt-item-input-representative'], [e['relation'] for e in edges if e['source'] == selected['id']])

    def test_complete_empty_original_target_state_is_not_missing_witness(self):
        _, _, _, witness = self.prepare()
        witness.update(targets=[], accepted_domain_ordinals=[], accepted_occurrence_ordinals=[])
        self.publish()
        self.project()
        _, nodes, edges = self.rows()
        selected = self.ordinary_selector(nodes)
        self.assertTrue(selected['properties']['acceptance_complete'])
        self.assertEqual([], [e for e in edges if e['source'] == selected['id']])

    def test_native_occurrence_and_domain_disagreement_refuse(self):
        changes = [lambda h,c,w: w.update(accepted_occurrence_ordinals=[1, 3, 6]),
                   lambda h,c,w: w.update(accepted_domain_ordinals=[1]),
                   lambda h,c,w: w.update(accepted_occurrence_ordinals=[1, 2, 3, 5, 6]),
                   lambda h,c,w: w.update(accepted_domain_ordinals=[True, 2])]
        for change in changes:
            with self.subTest(change=change):
                _, h, c, w = self.prepare()
                change(h, c, w)
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError): self.project()
                self.assertFalse(self.output.exists())

    def test_every_original_occurrence_is_required_even_with_matching_seals(self):
        def omit(h, candidates, w):
            row = candidates[1]
            row['occurrences'].pop(1)
            row['occurrence_count'] = 2
            row['occurrences_sha256'] = digest([{k:o[k] for k in ('recipe_record_sha256','pointer')}
                                               for o in row['occurrences']])
        changes = [omit, lambda h,c,w: c[1]['occurrences'].reverse(),
                   lambda h,c,w: c[1]['occurrences'][1].update(pointer=c[1]['occurrences'][0]['pointer']),
                   lambda h,c,w: c[0].update(domain_ordinal=True),
                   lambda h,c,w: h.update(occurrence_count=6),
                   lambda h,c,w: h.update(occurrences_sha256='f'*64),
                   lambda h,c,w: h['class_sha256'].update(CapabilityDispatcher='f'*64)]
        for change in changes:
            with self.subTest(change=change):
                _, h, c, w = self.prepare()
                change(h, c, w)
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError): self.project()
                self.assertFalse(self.output.exists())

    def test_missing_candidate_refuses_even_without_any_selector_witness(self):
        self.prepare()
        rows = self.categories['gt-ordinary-item-matching'][1]
        rows.pop()
        rows.pop(2)
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'coverage is incomplete'): self.project()

    def test_relevant_capabilities_original_or_copy_never_promote(self):
        changes = [lambda h,c,w: c[1]['occurrences'][1].update(original_capability_writer_count=1),
                   lambda h,c,w: c[1]['occurrences'][2].update(copy_capability_writer_count=1),
                   lambda h,c,w: c[1]['occurrences'][0].update(copy_capability_writer_count=False),
                   lambda h,c,w: w['targets'][0].update(capability_writer_count=1),
                   lambda h,c,w: w['targets'][0].update(tag={'tag_id':10,'value':{}})]
        for change in changes:
            with self.subTest(change=change):
                _, h, c, w = self.prepare()
                change(h, c, w)
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError): self.project()
                self.assertFalse(self.output.exists())

    def test_irrelevant_capabilities_do_not_block_proved_nonmatches(self):
        _, _, candidates, _ = self.prepare()
        for index in (0, 3, 4):
            candidates[index]['occurrences'][0].update(original_capability_writer_count=1, copy_capability_writer_count=2)
        self.publish()
        self.project()
        _, nodes, _ = self.rows()
        self.assertTrue(self.ordinary_selector(nodes)['properties']['acceptance_complete'])

    def test_later_unsupported_target_cannot_be_dropped_from_qualification(self):
        _, _, _, witness = self.prepare()
        witness['targets'].append(dict(deepcopy(witness['targets'][0]), capability_writer_count=1))
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'target is outside'): self.project()

    def test_cancellation_during_occurrence_admission_preserves_input(self):
        self.prepare()
        records = self.categories['gt-ordinary-item-matching'][1]
        recipes = self.categories['gt-recipes'][1]
        original = deepcopy((records, recipes))
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 30:
                raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            qualify_ordinary_item_matching(records, recipes, tuple(digest(row) for row in recipes),
                                            check_cancelled=cancel)
        self.assertEqual(original, (records, recipes))

    def test_any_changed_copy_identity_blocks_all_ordinary_qualification(self):
        _, _, candidates, _ = self.prepare()
        # A previously irrelevant candidate becomes a matching copied stack.
        candidates[0]['occurrences'][0]['copy_stack'].update(metadata=3)
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'changed candidate copy identities'): self.project()

    def test_copy_quantity_change_is_not_hidden_by_quantity_free_identity(self):
        _, _, candidates, _ = self.prepare()
        candidates[1]['occurrences'][0]['copy_stack']['count'] = 64
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'changed candidate copy counts'): self.project()

    def test_changed_copy_can_be_retained_when_no_selector_claims_qualification(self):
        _, _, candidates, _ = self.prepare()
        candidates[0]['occurrences'][0]['copy_stack'].update(metadata=3)
        self.categories['gt-ordinary-item-matching'][1].pop()
        self.publish()
        self.project()
        manifest, nodes, _ = self.rows()
        self.assertFalse(self.ordinary_selector(nodes)['properties']['acceptance_complete'])
        self.assertFalse(manifest['evidence_binding']['ordinary_item_matching']['copy_identities_unchanged'])
        self.assertTrue(manifest['evidence_binding']['ordinary_item_matching']['copy_counts_unchanged'])

    def test_unknown_class_or_custom_nbt_cannot_use_an_ordinary_witness(self):
        for mutation in ('class', 'nbt'):
            with self.subTest(mutation=mutation):
                value, header, candidates, witness = self.prepare()
                target = value['item_inputs'][0]
                if mutation == 'class': target['runtime_class'] = 'fixture.DerivedItemInput'
                else: target['nbt_matcher'] = 'custom'
                row = self.categories['gt-recipes'][1][0]
                row['semantic_sha256'] = digest(value)
                record_hash = digest(row)
                for candidate in candidates:
                    for occurrence in candidate['occurrences']: occurrence['recipe_record_sha256'] = record_hash
                    candidate['occurrences_sha256'] = digest([{k:o[k] for k in ('recipe_record_sha256','pointer')}
                                                               for o in candidate['occurrences']])
                all_occurrences = sorted([o for c in candidates for o in c['occurrences']], key=lambda o: (
                    '/item_inputs/' not in o['pointer'], '/chanced_item_outputs/' in o['pointer'], o['pointer']))
                header['occurrences_sha256'] = digest([{k:o[k] for k in ('recipe_record_sha256','pointer')} for o in all_occurrences])
                header['domain_sha256'] = digest([{k:c['occurrences'][0][k] for k in ('recipe_record_sha256','pointer')} for c in candidates])
                witness.update(recipe_record_sha256=record_hash, semantic_sha256=row['semantic_sha256'])
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'unqualified input class or custom NBT'): self.project()

    def test_previous_model_cannot_silently_qualify(self):
        _, header, _, _ = self.prepare()
        header['model'] = 'gt-2.8.10-forge-1.12.2-null-tag-zero-writer-item-matching-v1'
        self.publish()
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'original byte binding differs'):
            self.project()
        self.assertFalse(self.output.exists())

    def test_deferred_unknown_is_never_inferred_as_zero_writers(self):
        for subject in ('original', 'copy'):
            for writer_count in (0, None):
                with self.subTest(subject=subject, writer_count=writer_count):
                    _, _, candidates, _ = self.prepare()
                    # Even this irrelevant resource might change identity when
                    # its provider is eventually initialized; no global closure.
                    row = candidates[4]['occurrences'][0]
                    row[subject + '_capability_initialization_state'] = 'unknown'
                    row[subject + '_capability_writer_count'] = writer_count
                    self.publish()
                    with self.assertRaisesRegex(RecipeGraphProjectionError, 'initializ'):
                        self.project()
                    self.assertFalse(self.output.exists())

    def test_unknown_observation_without_witness_retains_incompleteness(self):
        _, _, candidates, _ = self.prepare()
        candidates[1]['occurrences'][1].update(original_capability_initialization_state='unknown',
                                             original_capability_writer_count=None)
        self.categories['gt-ordinary-item-matching'][1].pop()
        self.publish()
        self.project()
        manifest, nodes, _ = self.rows()
        self.assertFalse(self.ordinary_selector(nodes)['properties']['acceptance_complete'])
        self.assertFalse(manifest['evidence_binding']['ordinary_item_matching']['capability_initialization_complete'])

    def test_missing_malformed_or_history_dependent_initialization_state_refuses(self):
        mutations = [lambda c,w: c[1]['occurrences'][0].pop('original_initialized_stack'),
                     lambda c,w: c[1]['occurrences'][0].pop('original_capability_initialization_state'),
                     lambda c,w: c[1]['occurrences'][0].update(copy_capability_initialization_state=True),
                     lambda c,w: c[1]['occurrences'][0].update(copy_capability_initialization_state=['initialized']),
                     lambda c,w: c[1]['occurrences'][0].update(copy_capability_initialization_state='initialized-by-observer'),
                     lambda c,w: w['targets'][0].pop('stack_before_initialization'),
                     lambda c,w: w['targets'][0].update(capability_initialization_state='unknown')]
        for change in mutations:
            with self.subTest(change=change):
                _, _, c, w = self.prepare()
                change(c, w)
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError): self.project()
                self.assertFalse(self.output.exists())

    def test_original_initialization_visible_changes_cannot_qualify(self):
        changes = [dict(registry_name='fixture:changed'), dict(metadata=99), dict(item_damage=99),
                   dict(tag={'tag_id':10,'value':{}}), dict(count=64)]
        for change in changes:
            with self.subTest(change=change):
                _, _, candidates, _ = self.prepare()
                candidates[1]['occurrences'][0]['original_initialized_stack'].update(change)
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'changed initialization'):
                    self.project()
                self.assertFalse(self.output.exists())

    def test_copy_initialization_cannot_hide_identity_or_quantity_transition(self):
        for change in (dict(metadata=99), dict(count=64)):
            with self.subTest(change=change):
                _, _, candidates, _ = self.prepare()
                # Post-init copy agrees with the raw stack. Before-init copy
                # does not, so checking only the final state would be unsound.
                candidates[1]['occurrences'][0]['copy_before_initialization_stack'].update(change)
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'changed candidate copy'):
                    self.project()
                self.assertFalse(self.output.exists())

    def test_target_initialization_visible_changes_cannot_qualify(self):
        for change in (dict(metadata=99), dict(count=64), dict(tag={'tag_id':10,'value':{}})):
            with self.subTest(change=change):
                _, _, _, witness = self.prepare()
                witness['targets'][0]['stack_after_initialization'].update(change)
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'target changed'):
                    self.project()
                self.assertFalse(self.output.exists())

    def test_lazy_provider_original_archive_pins_are_required(self):
        mutations = [lambda h: h['artifacts'].pop('loliasm_sha256'),
                     lambda h: h['artifacts'].update(loliasm_sha256='f'*64),
                     lambda h: h['class_sha256'].update(LoliItemStackMixin='f'*64),
                     lambda h: h['class_sha256'].pop('IItemStackCapabilityDelayer')]
        for change in mutations:
            with self.subTest(change=change):
                _, header, _, _ = self.prepare()
                change(header)
                self.publish()
                with self.assertRaisesRegex(RecipeGraphProjectionError, 'original byte binding differs'):
                    self.project()

    def test_native_copy_policy_cannot_be_missing_or_silently_changed(self):
        for value in (None, True, 'initialize-first-collapsed-representative-only'):
            with self.subTest(value=value):
                _, header, _, _ = self.prepare()
                if value is None:
                    header.pop('native_copy_initialization_policy')
                else:
                    header['native_copy_initialization_policy'] = value
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError): self.project()
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
