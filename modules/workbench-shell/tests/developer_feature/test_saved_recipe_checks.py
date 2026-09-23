"""Saved recipe checks through the real supervisor and retained Work Session boundary."""
import json
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_saved_checks as saved
from workbench_core import check_storage
from workbench_profile_supersymmetry import developer_checks as policy
from workbench_project_intelligence.working_tree import capture_source_inputs
from workbench_shell.developer_context import verify_developer_owner_reference
from workbench_crucible.check_assertions import evaluate
from workbench_crucible.check_explanations import seal_explanation
from workbench_crucible.developer_checks import seal_result

PATH = 'groovy/postInit/chemistry/Probe.groovy'
SOURCE = "MIXER.recipeBuilder().inputs(item('minecraft:stone') * 2).outputs(item('minecraft:dirt')).duration(20).EUt(30).buildAndRegister()\n"
RECIPE_FIXTURE = r'''
if not error and (root/'groovy/workbenchChecks/RecipeCapture.groovy').exists():
    line = next(line for line in (root/'groovy/workbenchChecks/RecipeCapture.groovy').read_text().splitlines() if line.startswith('def spec ='))
    spec = json.loads(json.loads(line.split('gson.fromJson(',1)[1].rsplit(', Map)',1)[0]))
    resolution = {}
    for row in spec['identities']:
        key = json.dumps(row,sort_keys=True,separators=(',',':'))
        resolution[key] = [{'item':row['name'],'metadata':row['metadata']}]
    text = (root/'groovy/postInit/chemistry/Probe.groovy').read_text()
    actual = None
    if 'MIXER.recipeBuilder' in text:
        actual = {'map':'mixer','duration':int(re.search(r'duration\((\d+)\)',text).group(1)),'eut':30,
                  'item_inputs':[{'ore':None,'stacks':[{'item':'minecraft:stone','metadata':0}],'amount':2}],
                  'item_outputs':[{'item':'minecraft:dirt','metadata':0,'amount':1}], 'fluid_inputs':[],'fluid_outputs':[]}
    captured = dict(format='workbench-recipe-capture-v4',lifecycle=None,decisions=None,nonce=nonce, expectation_id=spec['expectation_id'], state='complete',phase='render-tick-after-load-complete',resolution=resolution,map='mixer',error=None,
                    records=[] if actual is None else [dict(id='r0',recipe=actual,lookup_active=True,category_present=True,unsupported_reason=None)],
                    queries=[dict(id='q0',accepting=[] if actual is None else ['r0'],winner_id=None if actual is None else 'r0',items=[dict(item='minecraft:stone',metadata=0,amount=2)],fluids=[],voltage_limit=2147483647,winner=actual,unsupported=False)])
    with (root/'logs/latest.log').open('a') as stream:
        stream.write('[WORKBENCH-RECIPE-CAPTURE]'+json.dumps(captured)+'\n')
'''


class SavedRecipeChecksTests(unittest.TestCase):
    def setUp(self):
        saved.SavedCheckTests.setUp(self)
        (self.pack/PATH).write_text(SOURCE)
        (self.template/'fixture.py').write_text(saved.FIXTURE.replace('time.sleep(30)',RECIPE_FIXTURE+'\ntime.sleep(30)'))
        self.image = check_storage.import_image(self.root,self.template,Path(sys.executable).resolve(),['fixture.py'],policy.binding(capture_source_inputs(self.pack)),excluded_roots=policy.descriptor()['excluded_roots'])

    command = saved.SavedCheckTests.command
    execute = saved.SavedCheckTests.execute

    def prepare_recipe(self, *, reference=None, absent=False):
        source_args = [] if reference is None else ['--reference',reference['attempt_id']]
        catalog = self.command('recipes','--path',PATH,*source_args)['result']
        row = catalog['recipes'][0]
        options = [] if reference is None else ['--recipe-reference',reference['attempt_id']]
        if absent: options.append('--absent')
        return self.command('prepare','--no-trace','--image',self.image['id'],'--recipe',row['id'],'--timeout','3',*options)['result']

    def test_saved_unchanged_modified_wrong_expectation_and_removed_source(self):
        first = self.execute(self.prepare_recipe())['result']
        self.assertEqual(first['assertions']['state'],'matched',first)
        second = self.execute(self.prepare_recipe())['result']
        comparison = self.command('compare',second['attempt_id'],'--reference',first['attempt_id'])['result']
        self.assertEqual(comparison['assertions']['state'],'unchanged')
        (self.pack/PATH).write_text(SOURCE.replace('duration(20)','duration(40)'))
        wrong = self.execute(self.prepare_recipe(reference=first))['result']
        self.assertEqual(wrong['assertions']['state'],'mismatched')
        report = wrong['assertions']['observation']['details']['explanation']
        self.assertIn('Duration (ticks): expected 20; observed 40', report['text'])
        with patch('workbench_shell.developer_checks.provider',side_effect=ValueError('Profile is not installed')):
            explained = self.command('explain',wrong['attempt_id'])['result']
            self.assertEqual(explained['explanation'],report)
            self.assertIn('Recipe assertion: mismatched',explained['text'])
            self.assertEqual(explained['result_id'],wrong['id'])
        changed = self.execute(self.prepare_recipe())['result']
        self.assertEqual(changed['assertions']['state'],'matched')
        comparison = self.command('compare',changed['attempt_id'],'--reference',first['attempt_id'])['result']
        self.assertEqual(comparison['assertions']['state'],'changed')
        (self.pack/PATH).write_text('// recipe removed by developer\n')
        response = self.execute(self.prepare_recipe(reference=changed,absent=True))
        result = response['result']
        self.assertEqual(result['assertions']['state'],'matched')
        with patch('workbench_shell.developer_checks.provider',side_effect=ValueError('Profile is not installed')):
            self.assertEqual(self.command('show',result['attempt_id'])['result'],result)
            verified = verify_developer_owner_reference(response['owner_record_ref'],self.selection,suite_root=saved.ROOT)
            self.assertEqual(verified['last_verified_state'],result['state'])
        self.assertFalse(self.command('show',first['attempt_id'])['presentation']['source_current'])
        self.assertEqual(result['cleanup']['state'],'trashed')

    def test_unsupported_sources_never_launch_and_selected_bytes_are_rechecked(self):
        (self.pack/PATH).write_text(SOURCE.replace('duration(20)','duration(dynamic())'))
        request = self.prepare_recipe()
        self.assertEqual(request['expectation']['support'],'unsupported')
        with self.assertRaisesRegex(ValueError,'unsupported'): self.execute(request)
        self.assertFalse((self.root/'.workbench/check-attempts'/request['attempt_id']/'started.json').exists())

    def test_retained_source_opens_after_live_edit_without_loading_profile(self):
        result = self.execute(self.prepare_recipe())['result']
        (self.pack/PATH).write_text('// edited after execution\n')
        with patch('workbench_shell.developer_checks.provider', side_effect=ValueError('profile removed')):
            view = self.command('source', result['attempt_id'], '--source', 'expectation:0')['result']
        self.assertTrue(view['read_only'])
        self.assertEqual(view['text'], SOURCE)
        self.assertEqual(view['result_id'], result['id'])
        with self.assertRaises(ValueError): self.command('source', result['attempt_id'], '--source', '../source:0')
        with self.assertRaises(ValueError): self.command('source', result['attempt_id'], '--source', 'expectation:1')

    def test_missing_recipe_capture_after_compiler_failure_stays_inconclusive(self):
        first = self.execute(self.prepare_recipe())['result']
        (self.pack/PATH).write_text('BROKEN\n')
        failed = self.execute(self.prepare_recipe(reference=first))['result']
        self.assertEqual(failed['state'],'failed')
        self.assertEqual(failed['assertions']['state'],'inconclusive')
        self.assertIn('no absence or registration failure is inferred', self.command('explain',failed['attempt_id'])['result']['text'])
        comparison = self.command('compare',failed['attempt_id'],'--reference',first['attempt_id'])['result']
        self.assertEqual(comparison['assertions']['state'],'not-comparable')

    def test_changed_retained_source_and_log_evidence_are_rejected(self):
        result = self.execute(self.prepare_recipe())['result']
        directory = self.root/'.workbench/check-attempts'/result['attempt_id']
        (directory/'logs/latest.log').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            self.command('show',result['attempt_id'])

    def test_resealed_explanation_source_must_still_match_retained_bytes(self):
        result = self.execute(self.prepare_recipe())['result']
        observation = deepcopy(result['assertions']['observation'])
        report = observation['details']['explanation']
        report['sections'][0]['sources'][0]['location']['sha256'] = 'f'*64
        observation['details']['explanation'] = seal_explanation(report['summary'],report['sections'],report['limitations'])
        result['assertions'] = evaluate(result['assertions']['expectation'],observation,result['execution'],result['interpretation'],result['error'])
        body = {key:value for key,value in result.items() if key!='id'}
        changed = seal_result(body)
        directory = self.root/'.workbench/check-attempts'/result['attempt_id']
        (directory/'result.json').write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'explanation source differs'):
            self.command('explain',result['attempt_id'])
