"""Assertion truth cannot be inferred from a startup transport or partial capture."""
from copy import deepcopy
import unittest

from workbench_crucible.check_assertions import evaluate, seal, validate_assertion, validate_expectation
from test_check_comparison import sample


class CheckAssertionsTests(unittest.TestCase):
    def setUp(self):
        self.plan = seal('check-expectation', {'format':'workbench-check-expectation-v1', 'candidate_id':'candidate:sha256:'+'a'*64,
            'source':{'candidate_id':'candidate:sha256:'+'a'*64,'reference':None}, 'subject':{'selector':{'fixture':'input'}},
            'mode':'present','support':'supported','reasons':[], 'expected':{'count':1,'lookup':True}})
        self.observation = {'state':'complete','facts':{'count':1,'lookup':True},'evidence':[{'log':'logs/latest.log','line':1}], 'details':{},'reasons':[]}
        self.run = sample(1)

    def assess(self, **changes):
        values = {'expectation':self.plan,'observation':self.observation,'execution':self.run['execution'],
                  'interpretation':self.run['interpretation'],'error':None} | changes
        return evaluate(**values)

    def test_matches_are_exactly_typed_and_never_promote_the_run(self):
        result = self.assess()
        self.assertEqual(result['state'],'matched')
        self.assertFalse(result['authority']['outcomes_promoted'])
        for changed in ({'count':2,'lookup':True},{'count':True,'lookup':True},{'count':1,'lookup':False}):
            self.assertEqual(self.assess(observation=self.observation | {'facts':changed})['state'],'mismatched')

    def test_missing_early_cancelled_or_incompatible_evidence_is_not_a_recipe_pass(self):
        for execution in ({'state':'closed','stop_reason':'failure-observed'}, {'state':'closed','stop_reason':'timed-out'}, {'state':'closure-unverified'}):
            self.assertEqual(self.assess(execution=execution)['state'],'inconclusive')
        self.assertEqual(self.assess(error='capture failed')['state'],'inconclusive')
        self.assertEqual(self.assess(interpretation=self.run['interpretation'] | {'complete_logs':False})['state'],'inconclusive')
        self.assertEqual(self.assess(observation=self.observation | {'state':'ambiguous'})['state'],'inconclusive')
        self.assertEqual(self.assess(observation=self.observation | {'state':'unsupported'})['state'],'unsupported')

    def test_sealed_expectation_and_assertion_tampering_is_rejected(self):
        changed = deepcopy(self.plan); changed['expected']['count'] = 2
        with self.assertRaises(ValueError): validate_expectation(changed)
        result = self.assess(); result['state'] = 'mismatched'
        with self.assertRaises(ValueError): validate_assertion(result,self.run['execution'],self.run['interpretation'],None)
        with self.assertRaises(ValueError): self.assess(observation=self.observation | {'facts':{}})
