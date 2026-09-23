"""Whole-program property fixtures and explicit receipt coverage; not a native oracle."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from axiom_material_authoring_cases import property_corpus,check
from axiom_material_program_cases import cases,identity

class MaterialPropertyValueTests(unittest.TestCase):
    def test_every_property_case_retains_the_whole_source_program(self):
        selected=property_corpus();base=cases()[0]['files']
        self.assertEqual(10,len(selected))
        for case in selected:
            self.assertEqual(base.keys(),case['files'].keys())
            self.assertEqual(base['groovy/runConfig.json'],case['files']['groovy/runConfig.json'])
            self.assertEqual(identity(case['files']),case['candidateIdentity'])
        self.assertEqual(1,sum(c.get('executionCompleted') is False for c in selected))
    def test_native_defaults_and_all_ten_mutable_scalar_getters_are_explicit(self):
        selected={c['name']:c for c in property_corpus()}
        tool=selected['property-tool-default']['expectedToolValues']['tool']
        self.assertEqual(10,len(tool));self.assertEqual(100,tool['toolDurability']['value'])
        self.assertEqual(10,tool['toolEnchantability']['value']);self.assertFalse(tool['unbreakable']['value'])
        self.assertFalse(selected['property-tool-gem-constructor']['expectedIngot'])
        self.assertTrue(selected['property-tool-pack-constructor']['expectedIngot'])
        self.assertEqual(-131,selected['property-tool-negative-values']['expectedToolValues']['tool']['toolDurability']['value'])
    def test_float_bits_preserve_signed_zero_and_explicit_nonfinite_values(self):
        selected={c['name']:c for c in property_corpus()}
        zero=selected['property-tool-signed-zero']['expectedToolValues']['tool']
        self.assertEqual('80000000',zero['toolSpeed']['rawBits']);self.assertEqual('00000000',zero['toolAttackSpeed']['rawBits'])
        values=selected['property-tool-nonfinite']['expectedToolValues']['tool']
        self.assertEqual('NaN',values['toolSpeed']['value']);self.assertEqual('ff800000',values['toolAttackSpeed']['rawBits'])
    def test_missing_property_values_cannot_satisfy_receipt_assertions(self):
        case=next(c for c in property_corpus() if c['name']=='property-tool-default')
        reference={'phase':'FROZEN','registeredMaterials':605,'executionCompleted':True,'coverageGaps':[],'nativeErrors':[],
            'materials':[{'color':case['expectedColor'],'properties':['tool','ingot'],'propertyValues':case['expectedToolValues']},
                         {'propertyValues':{}},{'propertyValues':{}}]}
        native={**deepcopy(reference),'cleanObservation':True,'candidateAdmissionViolations':[]}
        self.assertEqual([],check(case,reference,native))
        del native['materials'][0]['propertyValues']['tool']['toolSpeed']
        self.assertTrue(check(case,reference,native))
    def test_duplicate_property_preserves_both_throw_and_native_error_log(self):
        case=next(c for c in property_corpus() if c['name']=='property-tool-duplicate')
        reference={'phase':'CLOSED','registeredMaterials':605,'executionCompleted':False,'coverageGaps':[],
            'nativeErrors':case['expectedNativeErrors'],'failure':{'message':case['expectedNativeFailure']},
            'materials':[{'color':case['expectedColor'],'properties':['tool','ingot'],'propertyValues':case['expectedToolValues']},
                         {'propertyValues':{}},{'propertyValues':{}}]}
        native={**deepcopy(reference),'cleanObservation':False,'candidateAdmissionViolations':[],
                'nativeException':'java.lang.IllegalArgumentException: '+case['expectedNativeFailure']}
        self.assertEqual([],check(case,reference,native))
        native['nativeErrors']=[];self.assertTrue(check(case,reference,native))

if __name__=='__main__':unittest.main()
