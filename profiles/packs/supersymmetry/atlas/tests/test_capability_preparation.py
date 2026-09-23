"""Preparation transitions are distinct observations, never identity aliases."""
from copy import deepcopy
import hashlib
import json
import unittest

from workbench_profile_supersymmetry import capability_preparation as prep
from workbench_profile_supersymmetry import recipe_graphs
from workbench_profile_supersymmetry.recipe_graphs import RecipeGraphProjectionError
import test_recipe_graph_projection as fixture
from workbench_atlas_recipe_health import compare_runtime_recipe_graphs, open_recipe_health


def example():
    old = fixture.recipe()
    old['item_inputs'] = [fixture.selector(stack=fixture.item())]
    old['item_outputs'] = [{'ordinal': 0, 'value': fixture.item()}]
    old['chanced_item_outputs']['entries'] = []
    before = dict(record_type='gt-recipe', recipe_map='fixture_map', semantic_sha256=fixture.digest(old),
                  duplicate_ordinal=0, lookup_active=True, category_present=True, recipe=old)
    after = deepcopy(before)
    after['recipe']['item_outputs'][0]['value']['tag'] = {'tag_id': 10, 'value': {'UUID': {'tag_id': 4, 'value': 123}}}
    after['semantic_sha256'] = fixture.digest(after['recipe'])
    old_hash, new_hash = fixture.digest(before), fixture.digest(after)
    def occurrence(suffix):
        return dict(role='recipe-item-occurrence',
            before={'recipe_record_sha256': old_hash, 'pointer': '/records/0/recipe/' + suffix},
            after={'recipe_record_sha256': new_hash, 'pointer': '/records/0/recipe/' + suffix})
    target = dict(selector_ordinal=0, item_entry_ordinal=0, metadata_entry_ordinal=0,
                  tag_entry_ordinal=0, registry_name='fixture:tool', metadata=3, tag=None)
    target_ref = dict(role='ordinary-stored-target',
                     before=dict(target, recipe_record_sha256=old_hash),
                     after=dict(target, recipe_record_sha256=new_hash))
    first = dict(object_ordinal=0, stack_before=fixture.item(), stack_before_initializer=fixture.item(),
        stack_after_initializer=fixture.item(), stack_after=fixture.item(),
        initialization_state_before='initialized', initialization_state_before_initializer='initialized',
        initialization_state_after_initializer='initialized', initialization_state_after='initialized',
        initializer_invoked=False, references=[occurrence('item_inputs/0/item_stack_representatives/0'), target_ref])
    second = dict(object_ordinal=1, stack_before=fixture.item(), stack_before_initializer=fixture.item(),
        stack_after_initializer=deepcopy(after['recipe']['item_outputs'][0]['value']),
        stack_after=deepcopy(after['recipe']['item_outputs'][0]['value']),
        initialization_state_before='deferred', initialization_state_before_initializer='deferred',
        initialization_state_after_initializer='initialized', initialization_state_after='initialized',
        initializer_invoked=True, references=[occurrence('item_outputs/0/value')])
    body = dict(policy=prep.POLICY, state='complete', recipes_before=[before],
        recipe_bindings=[dict(recipe_map='fixture_map', before_record_sha256=old_hash, after_record_sha256=new_hash)],
        objects=[first, second], counts=dict(recipe_count=1, object_count=2, reference_count=3,
            occurrence_reference_count=2, stored_target_reference_count=1, initializer_invocation_count=1,
            changed_object_count=1, unknown_object_count=0))
    witness = dict(record_type='gt-ordinary-item-matching-selector', recipe_record_sha256=new_hash,
        selector_ordinal=0, targets=[dict(registry_name='fixture:tool', metadata=3, tag=None,
            stack_before_initialization=fixture.item(), capability_initialization_state='initialized')])
    return body, after, witness


class CapabilityPreparationTests(unittest.TestCase):
    def validate(self, body, after, witness=None, cancel=lambda: None):
        return prep.validate_preparation(body,
            before_hashes=tuple(fixture.digest(row) for row in body['recipes_before']),
            before_field_hashes=tuple({'recipe': fixture.digest(row['recipe'])} for row in body['recipes_before']),
            recipes=[after], recipe_hashes=(fixture.digest(after),), ordinary_records=[] if witness is None else [witness],
            validate_recipe=recipe_graphs._validate_recipe, check_cancelled=cancel)

    def test_changed_uuid_identity_is_retained_without_equivalence(self):
        body, after, witness = example()
        result = self.validate(body, after, witness)
        self.assertFalse(result['identity_equivalence_claimed'])
        self.assertEqual('after-capability-preparation', result['recipe_observation_state'])
        self.assertEqual(1, result['counts']['changed_object_count'])
        self.assertIsNone(body['recipes_before'][0]['recipe']['item_outputs'][0]['value']['tag'])
        self.assertIsNotNone(after['recipe']['item_outputs'][0]['value']['tag'])

    def test_omitted_raw_occurrence_refuses_even_with_adjusted_counts(self):
        body, after, _ = example()
        body['objects'][0]['references'].pop(0)
        body['counts']['occurrence_reference_count'] -= 1
        body['counts']['reference_count'] -= 1
        with self.assertRaisesRegex(prep.CapabilityPreparationError, 'omitted a raw'):
            self.validate(body, after)

    def test_wrong_raw_stack_binding_refuses(self):
        body, after, _ = example()
        body['objects'][1]['stack_after']['tag'] = None
        with self.assertRaisesRegex(prep.CapabilityPreparationError, 'differs from raw'):
            self.validate(body, after)

    def test_duplicate_reference_or_wrong_recipe_bijection_refuses(self):
        for change in (lambda b: b['objects'][0]['references'].append(deepcopy(b['objects'][0]['references'][0])),
                       lambda b: b['recipe_bindings'].append(deepcopy(b['recipe_bindings'][0]))):
            body, after, _ = example(); change(body)
            with self.assertRaises(prep.CapabilityPreparationError): self.validate(body, after)

    def test_unknown_state_is_retained_without_zero_writer_claim(self):
        body, after, _ = example()
        for key in ('initialization_state_before', 'initialization_state_before_initializer',
                    'initialization_state_after_initializer', 'initialization_state_after'):
            body['objects'][0][key] = 'unknown'
        body['counts']['unknown_object_count'] = 1
        self.assertEqual(1, self.validate(body, after)['counts']['unknown_object_count'])

    def test_representatives_can_reorder_within_the_same_original_selector(self):
        body, after, _ = example()
        extra = fixture.item(tag={'tag_id': 10, 'value': {'x': {'tag_id': 3, 'value': 3}}})
        changed = fixture.item(tag={'tag_id': 10, 'value': {'x': {'tag_id': 3, 'value': 9}}})
        before = body['recipes_before'][0]
        before['recipe']['item_inputs'][0]['item_stack_representatives'].append(extra)
        before['semantic_sha256'] = fixture.digest(before['recipe'])
        after['recipe']['item_inputs'][0]['item_stack_representatives'] = [deepcopy(extra), changed]
        after['semantic_sha256'] = fixture.digest(after['recipe'])
        old_hash, new_hash = fixture.digest(before), fixture.digest(after)
        for obj in body['objects']:
            for ref in obj['references']:
                ref['before']['recipe_record_sha256'] = old_hash
                ref['after']['recipe_record_sha256'] = new_hash
        body['recipe_bindings'][0].update(before_record_sha256=old_hash, after_record_sha256=new_hash)
        obj = body['objects'][0]
        obj.update(stack_after_initializer=deepcopy(changed), stack_after=deepcopy(changed),
                   initializer_invoked=True, initialization_state_before='deferred', initialization_state_before_initializer='deferred')
        obj['references'][0]['after']['pointer'] = '/records/0/recipe/item_inputs/0/item_stack_representatives/1'
        other = deepcopy(obj)
        other.update(object_ordinal=2, stack_before=deepcopy(extra), stack_before_initializer=deepcopy(extra),
                     stack_after_initializer=deepcopy(extra), stack_after=deepcopy(extra),
                     initializer_invoked=False, initialization_state_before='initialized', initialization_state_before_initializer='initialized',
                     references=[dict(role='recipe-item-occurrence',
                         before=dict(recipe_record_sha256=old_hash, pointer='/records/0/recipe/item_inputs/0/item_stack_representatives/1'),
                         after=dict(recipe_record_sha256=new_hash, pointer='/records/0/recipe/item_inputs/0/item_stack_representatives/0'))])
        body['objects'].append(other)
        body['counts'].update(object_count=3, reference_count=4, occurrence_reference_count=3,
                              initializer_invocation_count=2, changed_object_count=2)
        self.assertFalse(self.validate(body, after)['identity_equivalence_claimed'])

    def test_cross_slot_reference_swap_refuses_even_if_stacks_equal(self):
        body, after, _ = example()
        # Make both raw stacks equal, then exchange output/input locators. The
        # complete pointer set still matches, but original object slots do not.
        before = body['recipes_before'][0]
        after = deepcopy(before)
        common_hash = fixture.digest(after)
        body['recipe_bindings'][0]['after_record_sha256'] = common_hash
        for obj in body['objects']:
            for ref in obj['references']: ref['after']['recipe_record_sha256'] = common_hash
        body['objects'][1].update(stack_after=fixture.item(), stack_after_initializer=fixture.item())
        body['counts']['changed_object_count'] = 0
        first = body['objects'][0]['references'][0]['after']
        second = body['objects'][1]['references'][0]['after']
        first['pointer'], second['pointer'] = second['pointer'], first['pointer']
        with self.assertRaisesRegex(prep.CapabilityPreparationError, 'original slot'):
            self.validate(body, after)

    def test_deferred_final_state_failed_initializer_and_bool_counts_refuse(self):
        changes = [lambda b: b['objects'][1].update(initialization_state_after='deferred'),
                   lambda b: b['objects'][1].update(initialization_state_after_initializer='unknown'),
                   lambda b: b['counts'].update(changed_object_count=True),
                   lambda b: b['objects'][1].update(initializer_invoked=False)]
        for change in changes:
            body, after, _ = example(); change(body)
            with self.assertRaises(prep.CapabilityPreparationError): self.validate(body, after)

    def test_target_inventory_must_match_later_ordinary_witness(self):
        for change in (lambda w: w['targets'][0].update(metadata=99), lambda w: w.update(targets=[])):
            body, after, witness = example(); change(witness)
            with self.assertRaisesRegex(prep.CapabilityPreparationError, 'target'):
                self.validate(body, after, witness)

    def test_cancellation_remains_visible(self):
        class Cancelled(Exception): pass
        def cancel(): raise Cancelled()
        body, after, _ = example()
        with self.assertRaises(Cancelled): self.validate(body, after, cancel=cancel)


class CapabilityPreparationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.FiniteRecipeProjectionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.body, after, _ = example()
        self.f.categories['gt-recipes'][1][:] = [after]
        self.f.input_value.update(format='workbench-supersymmetry-branch-observation-input-v1',
            pack_source=deepcopy(recipe_graphs._BRANCH_PACK), platform=deepcopy(recipe_graphs._BRANCH_PLATFORM),
            runtime_artifacts=deepcopy(recipe_graphs._BRANCH_ARTIFACTS), candidate_lock_sha256='c'*64,
            adapter_profile_sha256='d'*64, observation_preparation=deepcopy(prep.DECLARATION))
        self.f.input_value['pack_binding_id'] = 'supersymmetry-branch-observation:sha256:' + fixture.digest(self.f.input_value['pack_source'])
        self.f.input_value['platform_binding_id'] = 'forge-branch-observation:sha256:' + fixture.digest({
            k: self.f.input_value[k] for k in ('platform', 'runtime_artifacts')})

    def publish(self, *, declared=True, include=True, modify=None):
        if not declared: self.f.input_value.pop('observation_preparation', None)
        self.f.publish()
        path = self.f.capture / 'manifest.json'; manifest = json.loads(path.read_bytes())
        envelope = {key: manifest[key] for key in prep._BINDINGS}
        envelope.update(format='workbench-forge-item-capability-preparation-v1', schema_version=1, preparation=self.body)
        checkpoint = dict(observation_preparation_policy=prep.POLICY, checkpoint_id='post-start-end-tick',
                          physical_side='dedicated_server', server_started=True, actual_event='ServerTickEvent.END')
        if modify: modify(envelope, checkpoint)
        payloads = {'checkpoint.json': checkpoint}
        if include: payloads[prep.PAYLOAD_FILE] = envelope
        for name, value in payloads.items():
            raw = fixture.encoded(value); (self.f.capture/name).write_bytes(raw)
            manifest['payloads'].append(dict(file=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        manifest['payloads'].sort(key=lambda row: row['file'])
        manifest.pop('manifest_sha256'); manifest['manifest_sha256'] = fixture.digest(manifest)
        path.write_bytes(fixture.encoded(manifest))

    def test_sealed_transition_adds_explicit_scope_and_preserves_source(self):
        self.publish()
        before = {p: p.read_bytes() for p in self.f.capture.iterdir()}
        self.f.project()
        manifest, nodes, _ = self.f.rows()
        self.assertEqual('after-capability-preparation', manifest['scope']['recipe_observation_state'])
        summary = manifest['evidence_binding']['capability_preparation']
        self.assertFalse(summary['identity_equivalence_claimed'])
        self.assertEqual(hashlib.sha256(before[self.f.capture/prep.PAYLOAD_FILE]).hexdigest(), summary['payload_sha256'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        resources = [row for row in nodes if row['kind']=='item-variant']
        self.assertEqual(2, len(resources))

    def test_missing_payload_or_undeclared_preparation_refuses(self):
        self.publish(include=False)
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'declaration'): self.f.project()
        self.publish(declared=False)
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'declaration'): self.f.project()

    def test_wrong_bound_launch_and_checkpoint_refuse(self):
        self.publish(modify=lambda e,c:e.update(launch_id='another-launch'))
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'binding'): self.f.project()
        self.publish(modify=lambda e,c:c.update(observation_preparation_policy='other-policy'))
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'checkpoint'): self.f.project()

    def test_orphan_checkpoint_and_null_declaration_refuse(self):
        self.publish(declared=False, include=False)
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'checkpoint declares'): self.f.project()
        self.f.input_value['observation_preparation'] = None
        self.publish(include=False)
        with self.assertRaisesRegex(RecipeGraphProjectionError, 'declaration'): self.f.project()

    def test_prepared_captures_compare_but_passive_scope_is_distinct(self):
        self.publish(); self.f.project()
        first = self.f.output
        first_manifest = json.loads((first/'manifest.json').read_bytes())
        self.f.output = self.f.home/'second'
        row = self.f.categories['gt-recipes'][1][0]
        row['recipe']['item_outputs'][0]['value']['tag']['value']['UUID']['value'] = 456
        row['semantic_sha256'] = fixture.digest(row['recipe'])
        new_hash = fixture.digest(row)
        self.body['recipe_bindings'][0]['after_record_sha256'] = new_hash
        for obj in self.body['objects']:
            for ref in obj['references']: ref['after']['recipe_record_sha256'] = new_hash
        for key in ('stack_after_initializer', 'stack_after'):
            self.body['objects'][1][key] = deepcopy(row['recipe']['item_outputs'][0]['value'])
        self.publish(); self.f.project()
        second_manifest = json.loads((self.f.output/'manifest.json').read_bytes())
        self.assertEqual(first_manifest['scope'], second_manifest['scope'])
        self.assertNotEqual(first_manifest['evidence_binding']['capability_preparation']['payload_sha256'],
                            second_manifest['evidence_binding']['capability_preparation']['payload_sha256'])
        with open_recipe_health(first) as before, open_recipe_health(self.f.output) as after:
            self.assertEqual('compatible', compare_runtime_recipe_graphs(before, after)['compatibility']['state'])
        prepared = self.f.output
        self.f.output = self.f.home/'passive'
        self.f.input_value.pop('observation_preparation')
        self.f.publish()
        # The generic fixture publisher does not delete old auxiliary files;
        # their absence from the new sealed payload table is authoritative.
        self.f.project()
        with open_recipe_health(prepared) as before, open_recipe_health(self.f.output) as after:
            report = compare_runtime_recipe_graphs(before, after)
            self.assertEqual('incomparable', report['compatibility']['state'])
            self.assertIn('graph-scope-mismatch', [r['code'] for r in report['compatibility']['blocking_differences']])


if __name__ == '__main__': unittest.main()
