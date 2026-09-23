"""Complete native-argument corpus and receipt assertions, not native parity by themselves."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from axiom_material_authoring_cases import argument_corpus,check
from axiom_material_program_cases import cases,identity


class MaterialArgumentTests(unittest.TestCase):
    def test_arguments_remain_complete_source_programs_without_security_cases(self):
        selected=argument_corpus();base=cases()[0]['files']
        self.assertEqual(14,len(selected))
        self.assertEqual({'argument-'+name for name in ('byte','short','long','float','double','big-integer','big-decimal',
            'formula-one-argument','formula-formatted','formula-null','gstring-argument','gstring-assignment','string-flags','typed-array-flags')},
            {case['name'] for case in selected})
        for case in selected:
            self.assertEqual(base.keys(),case['files'].keys())
            self.assertEqual(base['groovy/runConfig.json'],case['files']['groovy/runConfig.json'])
            self.assertEqual(case['candidateIdentity'],identity(case['files']))

    def test_formula_requires_the_actual_native_value_on_both_legs(self):
        case=next(c for c in argument_corpus() if c['name']=='argument-formula-null')
        original={'phase':'FROZEN','registeredMaterials':605,'executionCompleted':True,'coverageGaps':[],
            'nativeErrors':[],'materials':[{'color':case['expectedColor']},{'formula':None},{}]}
        native={**deepcopy(original),'cleanObservation':True,'candidateAdmissionViolations':[]}
        self.assertEqual([],check(case,original,native))
        for side in ('original','installed'):
            before,after=deepcopy(original),deepcopy(native)
            (before if side=='original' else after)['materials'][1]['formula']=''
            self.assertTrue(check(case,before,after))
            before,after=deepcopy(original),deepcopy(native)
            del (before if side=='original' else after)['materials'][1]['formula']
            self.assertTrue(check(case,before,after))

    def test_numeric_programs_assert_explicit_results_not_just_successful_execution(self):
        expected={'byte':7,'short':71,'long':123,'float':123,'double':123,'big-integer':21,'big-decimal':7}
        selected={c['name']:c for c in argument_corpus()}
        for name,color in expected.items():self.assertEqual(color,selected['argument-'+name]['expectedColor'])
        self.assertIn(b'new gregtech.api.unification.material.info.MaterialFlag[]',
            selected['argument-typed-array-flags']['files']['groovy/classes/MaterialEdits.groovy'])
        self.assertTrue(selected['argument-string-flags']['expectedPlate'])


if __name__=='__main__':unittest.main()
