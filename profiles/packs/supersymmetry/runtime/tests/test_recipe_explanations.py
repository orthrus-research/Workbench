"""Bounded explanations of observed facts are not causal source attribution."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from workbench_crucible.check_explanations import validate_explanation
from workbench_profile_supersymmetry import recipe_checks as recipes, recipe_explanations as explanations
from test_recipe_checks import SOURCE, PATH, capture, expectation, inputs, logs


class RecipeExplanationsTests(unittest.TestCase):
    def report(self, plan, value, source=None):
        observation = recipes.interpret(logs(value), 'nonce', plan)
        report = explanations.explain(inputs() if source is None else source, plan, observation)
        validate_explanation(report, plan, observation['evidence'])
        return report

    def test_winner_property_differences_are_readable_and_retained_with_evidence(self):
        original = inputs(); changed = inputs(SOURCE.replace('duration(20)', 'duration(40)'))
        row = recipes.catalog(original)['recipes'][0]
        plan = recipes.expectation(changed, original, row['id'], reference={'attempt_id':'fixture','result_id':'fixture'})
        value = capture(plan)
        value['records'][0]['recipe']['duration'] = 40
        report = self.report(plan, value, changed)
        self.assertIn('Duration (ticks): expected 20; observed 40', report['text'])
        self.assertIn('Selected recipe: r0', report['text'])
        self.assertIn('Evidence: logs/latest.log:', report['text'])
        registration = next(row for row in report['sections'] if row['id']=='registration-r0')
        self.assertEqual(len(registration['sources']), 1)
        self.assertEqual(registration['sources'][0]['basis'], 'literal-candidate')
        self.assertEqual(registration['sources'][0]['candidate_id'], plan['candidate_id'])
        self.assertIn('not verified runtime origins', registration['text'])

    def test_duplicate_source_candidates_are_not_arbitrarily_selected(self):
        source = inputs(SOURCE+SOURCE)
        row = recipes.catalog(source)['recipes'][0]
        plan = recipes.expectation(source, source, row['id'])
        report = self.report(plan, capture(plan), source)
        registration = next(row for row in report['sections'] if row['id']=='registration-r0')
        self.assertEqual(len(registration['sources']), 2)
        self.assertNotEqual(registration['sources'][0]['location'], registration['sources'][1]['location'])

    def test_category_only_competitor_and_query_winner_are_distinct(self):
        plan = expectation(); value = capture(plan)
        competitor = deepcopy(value['records'][0]); competitor.update(id='r1', lookup_active=False)
        competitor['recipe']['duration'] = 90
        value['records'].append(competitor)
        value['queries'][0]['accepting'].append('r1')
        report = self.report(plan,value)
        self.assertIn('Active lookup membership: False; category membership: True', report['text'])
        self.assertIn('Accepting registration references: r0, r1', report['text'])
        self.assertIn('Selected recipe: r0', report['text'])
        self.assertIn('does not prove overwrite or rejection', report['text'])

    def test_multiple_queries_retain_their_own_winners_and_acceptance_links(self):
        plan = expectation(); value = capture(plan)
        second = deepcopy(value['records'][0]); second['id']='r1'; second['recipe']['duration']=50
        value['records'].append(second)
        value['queries'].append(deepcopy(value['queries'][0]) | {'id':'q1','accepting':['r1'],'winner_id':'r1','winner':second['recipe']})
        report = self.report(plan,value)
        queries = [row for row in report['sections'] if row['id'].startswith('lookup-')]
        self.assertIn('equals the expectation',queries[0]['title'])
        self.assertIn('differs from the expectation',queries[1]['title'])
        self.assertIn('Selected recipe: r1',queries[1]['text'])

    def test_absence_partial_and_unsupported_are_not_registration_failure_claims(self):
        plan = expectation(mode='absent'); value = capture(plan)
        value['records']=[]; value['queries'][0].update(accepting=[],winner=None,winner_id=None)
        self.assertIn('No recipe selected by the bounded lookup', self.report(plan,value)['text'])
        value['state']='incomplete'; value['error']='capture timed out'
        report = self.report(plan,value)
        self.assertIn('no absence or registration failure is inferred',report['summary'])
        self.assertFalse(any(row['id'].startswith('lookup-') for row in report['sections']))
        value=capture(plan); value['records'][0].update(recipe=None,unsupported_reason='nonconsumable input')
        value['queries'][0].update(winner=None,unsupported=True)
        self.assertIn('nonconsumable input',self.report(plan,value)['text'])

    def test_duplicate_runtime_occurrences_remain_ambiguous_but_inspectable(self):
        plan=expectation(); value=capture(plan)
        value['records'].append(deepcopy(value['records'][0]) | {'id':'r1'})
        value['queries'][0]['accepting'].append('r1')
        observation=recipes.interpret(logs(value),'nonce',plan)
        self.assertEqual(observation['state'],'ambiguous')
        self.assertIn('2 exact expectation match(es)',self.report(plan,value)['text'])

    def test_references_are_local_and_comparison_preserves_semantic_multiplicity(self):
        plan=expectation(); first=capture(plan); second=deepcopy(first)
        second['records'][0]['id']='r8'; second['queries'][0].update(id='q9',winner_id='r8',accepting=['r8'])
        left=recipes.interpret(logs(first),'nonce',plan)['details']['observed']
        right=recipes.interpret(logs(second),'nonce',plan)['details']['observed']
        self.assertEqual(left,right)
        second['records'].append(deepcopy(second['records'][0]) | {'id':'r9'})
        second['queries'][0]['accepting'].append('r9')
        duplicate=recipes.interpret(logs(second),'nonce',plan)['details']['observed']
        self.assertNotEqual(left,duplicate)

    def test_forged_missing_and_duplicate_capture_links_are_incomplete(self):
        plan=expectation()
        for field in ('duplicate-record','unknown-winner','unknown-acceptance','duplicate-query','unreferenced','false-null'):
            value=capture(plan)
            if field=='duplicate-record': value['records'].append(deepcopy(value['records'][0]))
            if field=='unknown-winner': value['queries'][0]['winner_id']='r1'
            if field=='unknown-acceptance': value['queries'][0]['accepting']=['r1']
            if field=='duplicate-query': value['queries'].append(deepcopy(value['queries'][0]))
            if field=='unreferenced': value['records'].append(deepcopy(value['records'][0]) | {'id':'r1'})
            if field=='false-null': value['queries'][0]['winner_id']=None
            self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['state'],'incomplete',field)

    def test_source_and_registration_display_bounds_are_explicit_not_absence(self):
        plan=expectation(); value=capture(plan)
        for number in range(1,67):
            value['records'].append(deepcopy(value['records'][0]) | {'id':f'r{number}'})
            value['queries'][0]['accepting'].append(f'r{number}')
        with patch.object(explanations,'MAX_SOURCE_BYTES',1):
            report=self.report(plan,value)
        self.assertIn('additional files were not inspected',report['text'])
        self.assertIn('3 further occurrences remain in the retained capture',report['text'])
        self.assertIn('unattributed within inspected source',report['text'])

    def test_repeated_output_quantities_are_not_collapsed_or_arbitrarily_paired(self):
        value=capture(expectation())['records'][0]['recipe']
        value['fluid_outputs']=[{'name':'coolant','amount':10000}]
        changed=deepcopy(value); changed['duration']=240; changed['eut']=60
        changed['fluid_outputs']=[{'name':'coolant','amount':9000},{'name':'coolant','amount':500}]
        rows=explanations.differences(value,changed)
        self.assertIn({'label':'Fluid output · coolant amount','expected':'10000','observed':'occurrence amounts [500, 9000]'},rows)
        self.assertEqual(explanations.differences(changed,deepcopy(changed)),[])

    def test_presentation_byte_and_selector_limits_preserve_explicit_omissions(self):
        sections=[explanations._section('expectation','Expected declaration')]
        sections.extend(explanations._section(f'lookup-q{i}','Captured lookup',notes=['x'*4000]*20) for i in range(40))
        sections.append(explanations._section('wide','Large selector',properties=[dict(label='x'*5000,expected='1',observed='2')]))
        report=explanations._bounded_report('Captured evidence',sections)
        self.assertIn('section(s) omitted',report['text'])
        self.assertIn('Over-bound property/selector text omitted',report['text'])
        validate_explanation(report,expectation(),[])

    def test_old_capture_format_is_not_adapted(self):
        plan=expectation(); value=capture(plan)
        value.pop('format')
        self.assertEqual(recipes.interpret(logs(value),'nonce',plan)['state'],'incomplete')
