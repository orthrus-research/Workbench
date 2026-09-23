"""Compiler-dependency witness validation; these tests do not prove native parity."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import axiom_compiler_linkage_conformance as linkage


class CompilerLinkageTests(unittest.TestCase):
    def test_exact_source_target_forms(self):
        self.assertEqual('a.Target',linkage.mixin_target('import a.Target;\n@Mixin(value = Target.class, remap = false)'))
        self.assertEqual('a.Outer$2',linkage.mixin_target('@Mixin(targets = "a/Outer$2", remap = false)'))
        self.assertEqual('groovy.lang.MetaClassImpl',linkage.mixin_target(
            '\nimport groovy.lang.*;\n@Mixin(value = MetaClassImpl.class, remap = false)'))

    def test_ambiguous_or_changed_source_is_not_guessed(self):
        for source in ('@Mixin(value = Target.class, remap = false)',
                       'import a.Target;\nimport b.Target;\n@Mixin(value = Target.class, remap = false)',
                       '\nimport groovy.lang.*;\n@Mixin(value = Other.class, remap = false)',
                       'import a.Target;\n@Mixin(value = Target.class, remap = true)'):
            with self.subTest(source=source),self.assertRaises(ValueError):linkage.mixin_target(source)

    def test_each_core_omission_changes_only_one_selected_dispatch_anchor(self):
        source='\n'.join('case '+visitor+'.CLASS_NAME: apply(); break;' for visitor in linkage.CORE)
        for visitor in linkage.CORE:
            changed=linkage.omit_core_dispatch(source,visitor)
            self.assertEqual(source,changed.replace('case "axiom.omitted.'+visitor+'":','case '+visitor+'.CLASS_NAME:'))
            self.assertEqual(len(linkage.CORE)-1,changed.count('.CLASS_NAME:'))
        for source,visitor in ((source,'UnknownVisitor'),('',linkage.CORE[0]),(source+'\n'+source,linkage.CORE[0])):
            with self.assertRaises(ValueError):linkage.omit_core_dispatch(source,visitor)

    def test_baseline_requires_applied_linkage_and_clean_complete_program(self):
        response={'status':'incomplete','result':{'groovyExecutionQualified':False,
            'bootstrap':{'admitted':True,'compilerLinkage':{'status':'observed','missing':[]}},
            'execution':{'cleanObservation':True},'nativeOutcome':'completed-without-observed-error'}}
        self.assertEqual([],linkage.check_response(response,4))
        for field,value in (('nativeOutcome','incomplete'),('groovyExecutionQualified',True),('execution',{})):
            changed=deepcopy(response);changed['result'][field]=value
            self.assertTrue(linkage.check_response(changed,4))
        changed=deepcopy(response);changed['result']['bootstrap']['compilerLinkage']['missing']=[{}]
        self.assertTrue(linkage.check_response(changed,4))
        self.assertTrue(linkage.check_response(response,0))

    def test_omission_requires_specific_dependency_and_no_native_compilation(self):
        omission={'kind':'mixin','target':'a.Target','required':'a.Mixin'}
        response={'status':'incomplete','result':{'groovyExecutionQualified':False,
            'bootstrap':{'admitted':False,'compilerInvoked':False,
                'compilerLinkage':{'status':'incomplete','missing':[omission]}},
            'candidateCompilationStarted':False,'nativeOutcome':'not-run'}}
        self.assertEqual([],linkage.check_response(response,4,omission))
        for field,value in (('candidateCompilationStarted',True),('nativeOutcome','source-error'),('execution',{})):
            changed=deepcopy(response);changed['result'][field]=value
            self.assertTrue(linkage.check_response(changed,4,omission))
        for field in ('kind','target','required'):
            changed=deepcopy(response);changed['result']['bootstrap']['compilerLinkage']['missing'][0][field]='wrong'
            self.assertTrue(linkage.check_response(changed,4,omission))


if __name__=='__main__':unittest.main()
