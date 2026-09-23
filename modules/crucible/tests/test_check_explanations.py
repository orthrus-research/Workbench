"""Retained generic explanation integrity without profile/domain imports."""
from copy import deepcopy
import unittest

from workbench_crucible.check_explanations import seal_explanation, validate_explanation


class CheckExplanationsTests(unittest.TestCase):
    def setUp(self):
        self.plan={'candidate_id':'current','source':{'candidate_id':'expected'}}
        self.evidence=[{'log':'logs/latest.log','line':5}]
        self.sections=[dict(id='observation',title='Captured mismatch',notes=['Not a causal conclusion.'],
                            properties=[dict(label='Duration',expected='200',observed='240')],evidence=self.evidence,sources=[])]

    def test_structured_differences_have_one_reproducible_plain_text_rendering(self):
        value=seal_explanation('Captured evidence',self.sections,['No qualification.'])
        self.assertEqual(validate_explanation(value,self.plan,self.evidence),value)
        self.assertIn('Duration: expected 200; observed 240',value['text'])
        self.assertIn('logs/latest.log:5',value['text'])

    def test_text_structure_and_source_reference_tampering_are_rejected(self):
        original=seal_explanation('Captured evidence',self.sections,['No qualification.'])
        for field in ('text','section-text','property','evidence','duplicate','candidate'):
            value=deepcopy(original)
            if field=='text': value['text']='Invented cause'
            if field=='section-text': value['sections'][0]['text']='Invented cause'
            if field=='property': value['sections'][0]['properties'][0]['observed']='200'
            if field=='evidence': value['sections'][0]['evidence']=[{'log':'missing.log','line':5}]
            if field=='duplicate': value['sections'].append(deepcopy(value['sections'][0]))
            if field=='candidate': value['sections'][0]['sources']=[dict(label='source',basis='candidate',candidate_id='foreign',location={})]
            with self.assertRaises(ValueError,msg=field): validate_explanation(value,self.plan,self.evidence)

    def test_oversized_or_control_character_text_is_rejected(self):
        for text in ('x'*5000,'bad\x00text'):
            value=seal_explanation(text,self.sections,['No qualification.'])
            with self.assertRaises(ValueError): validate_explanation(value,self.plan,self.evidence)
