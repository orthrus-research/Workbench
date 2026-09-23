"""Literal recipe admission, independent capture, and conservative interpretation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from workbench_profile_supersymmetry import recipe_checks as recipes, developer_checks as policy
from workbench_project_intelligence.working_tree import SourceInputs
from workbench_pack_program_studio.source_locations import verify_source_location

SOURCE = """import static prePostInit.Recipemaps.*
MIXER.recipeBuilder().inputs(item('minecraft:stone') * 2).outputs(item('minecraft:dirt')).duration(20).EUt(30).buildAndRegister()
"""
PATH = 'groovy/postInit/Check.groovy'


def inputs(text=SOURCE):
    files = ((PATH, text.encode()), ('groovy/runConfig.json', b'{"loaders":{"postInit":["postInit/"]}}'))
    return SourceInputs(json.dumps({'root_uri': 'file:///fixture', 'revision': 'a'*40, 'dirty': True}), files, tuple((name, 0o100644) for name, raw in files))


def expectation(text=SOURCE, mode='present'):
    source = inputs(text)
    row = recipes.catalog(source)['recipes'][0]
    return recipes.expectation(source, source, row['id'], mode=mode, reference=None if mode=='present' else {'attempt_id': 'check-'+'a'*32, 'result_id': 'fixture'})


def capture(plan):
    resolution = {}
    for field in ('item_inputs', 'item_outputs'):
        for row in plan['subject']['recipe'][field]:
            resolution[recipes.canonical({key: row[key] for key in ('kind','name','metadata')})] = [{'item':row['name'],'metadata':row['metadata']}]
    recipe = recipes._normalized(plan['subject']['recipe'], resolution)
    return {'format':'workbench-recipe-capture-v4','lifecycle':None,'decisions':None,'nonce':'nonce','expectation_id':plan['id'],'state':'complete','phase':'render-tick-after-load-complete',
            'resolution':resolution,'records':[{'id':'r0','recipe':recipe,'lookup_active':True,'category_present':True,'unsupported_reason':None}],
            'queries':[{'id':'q0','accepting':['r0'],'winner_id':'r0','items':[{'item':'minecraft:stone','metadata':0,'amount':2}], 'fluids':[], 'voltage_limit':2147483647, 'winner':recipe,'unsupported':False}],
            'map':'mixer','error':None}


def logs(value, before=False):
    marker = recipes.PREFIX + json.dumps(value) + '\n'
    checkpoint = 'Forge Mod Loader has successfully loaded 2 mods\n' + policy.PREFIX + json.dumps(dict(nonce='nonce',side='CLIENT',items=2,fluids=3)) + '\n'
    return {name:{'state':'captured','text':text} for name,text in {'logs/latest.log':marker+checkpoint if before else checkpoint+marker,'logs/groovy.log':''}.items()}


class RecipeChecksTests(unittest.TestCase):
    def test_exact_literal_source_and_locations_not_offsets_as_identity(self):
        source = inputs('// λ\r\n' + SOURCE.replace('\n','\r\n'))
        row = recipes.catalog(source)['recipes'][0]
        self.assertEqual(row['support'],'supported')
        self.assertEqual(row['recipe']['duration'],20)
        verify_source_location(source.sources[PATH], row['location'])
        changed = recipes.catalog(inputs(SOURCE.replace('duration(20)','duration(40)')))['recipes'][0]
        self.assertNotEqual(changed['id'], row['id'])
        self.assertEqual(recipes.catalog(source, 'groovy/postInit/missing.groovy')['recipes'], [])

    def test_dynamic_and_specialized_sources_are_not_evaluated_or_silently_dropped(self):
        for text in (SOURCE.replace('duration(20)','duration(compute())'), 'if(false)\n'+SOURCE,
                     SOURCE.replace('MIXER.recipe','MACERATOR.recipe'), SOURCE.replace('.duration(20)', '.circuitMeta(1).duration(20)'),
                     SOURCE.replace("item('minecraft:stone')", "item(unknown())"), 'MIXER = customMap\n'+SOURCE,
                     SOURCE.replace("item('minecraft:stone')", "item('minecraft:stone', 32767)")):
            row = recipes.catalog(inputs(text))['recipes'][0]
            self.assertEqual(row['support'],'unsupported',text)
            self.assertTrue(row['reasons'])
        self.assertEqual(recipes.catalog(inputs('// ' + SOURCE.replace('\n','\n// ')))['recipes'], [])

    def test_expectation_requires_exact_current_or_explicit_retained_source(self):
        original = inputs(); selected = recipes.catalog(original)['recipes'][0]
        with self.assertRaisesRegex(ValueError,'changed'):
            recipes.expectation(inputs(SOURCE+'// edit'), inputs(SOURCE+'// edit'), selected['id'])
        with self.assertRaisesRegex(ValueError,'reference'):
            recipes.expectation(original, original, selected['id'], mode='absent')

    def test_probe_has_no_expected_output_amount_duration_or_eut_values(self):
        plan = expectation(); script = recipes.probe(plan,'nonce').decode()
        line = next(line for line in script.splitlines() if line.startswith('def spec ='))
        encoded = line.split('gson.fromJson(',1)[1].rsplit(', Map)',1)[0]
        spec = json.loads(json.loads(encoded))
        self.assertNotIn('duration', recipes.canonical(spec))
        self.assertNotIn('eut', recipes.canonical(spec))
        self.assertNotIn('item_outputs', spec['selector'])
        self.assertIn('LoaderState.AVAILABLE', script)
        self.assertIn('event.@phase == TickEvent.Phase.END', script)
        self.assertIn('findRecipe(Integer.MAX_VALUE as long, queryItems, queryFluids, false)',script)
        self.assertNotIn('Blueprint',script)

    def test_independent_observation_matches_or_disagrees_with_expected_properties(self):
        plan = expectation(); value = capture(plan)
        observed = recipes.interpret(logs(value),'nonce',plan)
        self.assertEqual(observed['state'],'complete')
        self.assertEqual(observed['facts'],plan['expected'])
        value['records'][0]['recipe']['duration'] = 40
        changed = recipes.interpret(logs(value),'nonce',plan)
        self.assertEqual(changed['facts']['exact_registrations'],0)
        self.assertFalse(changed['facts']['lookup_satisfies_expectation'])
        self.assertEqual(changed['details']['observed']['records'][0]['recipe']['duration'],40)

    def test_duplicate_occurrences_and_unsupported_winners_remain_explicit(self):
        plan = expectation(); value = capture(plan)
        value['records'].append(deepcopy(value['records'][0]) | {'id':'r1'})
        value['queries'][0]['accepting'].append('r1')
        self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['state'],'ambiguous')
        value = capture(plan); value['records'][0]['recipe'] = None
        value['records'][0]['unsupported_reason'] = 'Unsupported input semantics'
        value['queries'][0].update(winner=None,unsupported=True)
        self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['state'],'unsupported')

    def test_contradictory_inventory_or_error_cannot_grant_absence(self):
        plan = expectation(mode='absent')
        for change in ('error','inactive','unknown-winner','membership'):
            value = capture(plan)
            if change == 'error': value['error'] = 'partial inventory'
            if change == 'inactive': value['records'][0]['lookup_active'] = False
            if change == 'unknown-winner': value['records'] = []
            if change == 'membership': value['records'][0]['category_present'] = 1
            self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['state'],'incomplete',change)

    def test_output_changes_do_not_change_query_resolution(self):
        first = expectation(); second = expectation(SOURCE.replace('minecraft:dirt','minecraft:gravel'))
        left = recipes.interpret(logs(capture(first)),'nonce',first)['details']
        right = recipes.interpret(logs(capture(second)),'nonce',second)['details']
        self.assertEqual(left['query_resolution'],right['query_resolution'])
        self.assertNotEqual(left['resolution'],right['resolution'])

    def test_runtime_json_key_and_component_order_are_not_semantic_changes(self):
        plan = expectation(); value = capture(plan)
        value['resolution'] = {json.dumps(json.loads(key), sort_keys=False, indent=1): row for key,row in value['resolution'].items()}
        self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['facts'],plan['expected'])

    def test_absence_needs_complete_capture_not_missing_or_prestartup_markers(self):
        plan = expectation(mode='absent'); value = capture(plan)
        value['records'] = []; value['queries'][0].update(winner=None,winner_id=None,accepting=[])
        self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['facts'],plan['expected'])
        self.assertEqual(recipes.interpret(logs(value,True),'nonce',plan)['state'],'incomplete')
        self.assertEqual(recipes.interpret(logs(value),'different',plan)['state'],'incomplete')
        bad = logs(value); bad['logs/latest.log']['text'] += recipes.PREFIX+json.dumps(value)+'\n'
        self.assertEqual(recipes.interpret(bad,'nonce',plan)['state'],'incomplete')

    def test_startup_waits_for_postload_capture_and_keeps_unknown_errors(self):
        plan = expectation(); value = capture(plan); records = logs(value)
        self.assertEqual(policy.observe(inputs(),records,'nonce',runtime_root=Path('/run'),expectation=plan)['state'],'checkpoint')
        records = logs(value,True)
        self.assertEqual(policy.observe(inputs(),records,'nonce',runtime_root=Path('/run'),expectation=plan)['state'],'running')
