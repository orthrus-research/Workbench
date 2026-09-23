"""Source custody and fail-closed checker tests, not native Groovy parity evidence."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import axiom_groovy_language_sources as source
import build_axiom_groovy_language as build
import axiom_groovy_language_conformance as conformance
import build_axiom_native_materials as compiler


class GroovyLanguageSourceTests(unittest.TestCase):
    def test_full_mixin_and_core_visitor_inventory(self):
        self.assertEqual(9,len(source.MIXINS))
        self.assertEqual(len(source.CLASSES),len(set(source.CLASSES)))
        for name in source.MIXINS:
            self.assertIn('core/mixin/groovy/'+name,source.CLASSES)
        for name in ('InvokerHelperVisitor','StaticVerifierVisitor','CachedClassFieldsVisitor',
                     'CachedClassMethodsVisitor','CachedClassConstructorsVisitor'):
            self.assertIn('core/visitors/'+name,source.CLASSES)
        for name in ('helper/GroovyHelper','sandbox/GroovyScriptSandbox','sandbox/ScriptModContainer',
                     'core/GroovyScriptTransformer','sandbox/transformer/GroovyScriptTransformer',
                     'api/IIngredient','api/IResourceStack','api/IMarkable'):
            self.assertIn(name,source.CLASSES)

    def test_source_reader_uses_selected_git_objects(self):
        groovy,gt=Path('groovyscript'),Path('gtceu')
        with patch.object(source,'git',return_value=b'original bytes') as read:
            original=source.read_sources(groovy,gt)
        self.assertIn('groovy:buildscript.properties',original)
        revisions=source.selected_revisions()
        for call in read.call_args_list:
            root,command,path=call.args
            self.assertEqual('show',command)
            self.assertTrue(path.startswith(revisions['groovyscript' if root==groovy else 'gtceu']+':'))
            self.assertNotIn('HEAD',path)

    def test_mapping_and_configuration_are_immutable_source_inputs(self):
        with patch.object(source,'git',return_value=b'resource') as read:
            resources=source.read_resources(Path('groovy'))
        self.assertEqual({'assets/groovyscript/mappings.srg','mixin.groovyscript.json'},set(resources))
        self.assertEqual(2,read.call_count)
        for call in read.call_args_list:
            self.assertTrue(call.args[2].startswith(source.selected_revisions()['groovyscript']+':src/main/resources/'))

    def test_executable_annotations_and_method_body_are_preserved(self):
        text='@Nullable @Mixin(value = Target.class) @SideOnly(Side.SERVER) @GroovyBlacklist\npublic static int f() { return 12; }'
        actual=source.strip(text)
        self.assertNotIn('@Nullable',actual)
        for retained in ('@Mixin(value = Target.class)','@SideOnly(Side.SERVER)','@GroovyBlacklist','return 12;'):
            self.assertIn(retained,actual)

    def test_ingredient_import_preserves_type_and_values_with_explicit_unsupported_ports(self):
        text='''import com.cleanroommc.groovyscript.helper.ingredient.OrIngredient;
import net.minecraftforge.common.ForgeHooks;
public interface IIngredient extends IResourceStack, Predicate<ItemStack>, IMarkable {
    default ItemStack applyTransform(ItemStack matchedInput) { return ForgeHooks.getContainerItem(matchedInput); }
    default IIngredient or(IIngredient ingredient) { return new OrIngredient(); }
    default boolean isCase(ItemStack ingredient) { return test(ingredient); }
    default int amount(ItemStack ingredient) { return ingredient.getCount(); }
    default boolean empty(ItemStack stack) { return stack.isEmpty(); }
    Object a = ItemStack.EMPTY;
    Object b = Ingredient.EMPTY;
}'''
        fields={('net/minecraft/item/ItemStack','EMPTY'):'field_stack',
                ('net/minecraft/item/crafting/Ingredient','EMPTY'):'field_ingredient'}
        methods={('net/minecraft/item/ItemStack','getCount'):{'func_count'},
                 ('net/minecraft/item/ItemStack','isEmpty'):{'func_empty'}}
        with patch.object(source,'native_symbols',return_value=(fields,methods)):
            actual=source.ingredient_source(text,'exact mappings')
        self.assertIn('extends IResourceStack, Predicate<ItemStack>, IMarkable',actual)
        self.assertIn('return test(ingredient);',actual)
        for value in ('ItemStack.field_stack','Ingredient.field_ingredient','ingredient.func_count()', 'stack.func_empty()'):
            self.assertIn(value,actual)
        for gap in source.INGREDIENT_BOUNDARIES.values():
            self.assertIn('throw '+source.HOST+'.unsupported("'+gap+'")',actual)
        self.assertNotIn('new OrIngredient',actual)
        self.assertNotIn('ForgeHooks',actual)

    def test_ingredient_projection_refuses_missing_original_method_anchor(self):
        with self.assertRaises(ValueError):
            source.ingredient_source('interface IIngredient {}','')

    def test_recipe_binds_transitive_inputs(self):
        frozen=build.recipe_inputs()
        for name in ('tools/build_axiom_groovy_language.py','tools/axiom_groovy_language_sources.py',
                     'tools/build_axiom_material_api.py','tools/axiom_material_api_sources.py',
                     'tools/build_axiom_native_materials.py','tools/axiom_runtime.py',
                     'modules/axiom/sources/material-program.lock.json',
                     'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeBoundary.java'):
            self.assertEqual((ROOT/name).read_bytes(),frozen[ROOT/name])

    def test_existing_output_refused_before_toolchain_or_source_reads(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(build,'verify_runtime') as verify:
                with self.assertRaisesRegex(ValueError,'must be new'):
                    build.build(*([root]*8))
                verify.assert_not_called()

    def test_qualified_source_paths_keep_same_simple_names(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(compiler.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')) as run:
                compiler.compile_sources(root,{'one/Thing':'package one; class Thing {}',
                                                'two/Thing':'package two; class Thing {}'},[],root/'out')
            paths=[value for value in run.call_args.args[0] if value.endswith('.java')]
            self.assertEqual([str(root/'out/source/one/Thing.java'),str(root/'out/source/two/Thing.java')],paths)
            self.assertTrue((root/'out/source/one/Thing.java').exists())
            self.assertTrue((root/'out/source/two/Thing.java').exists())

    def test_compiler_source_paths_cannot_escape_staging(self):
        for name in ('../outside','/absolute','a//b','a/../../b',''):
            with self.subTest(name=name),TemporaryDirectory() as temporary:
                with patch.object(compiler.subprocess,'run') as run:
                    with self.assertRaisesRegex(ValueError,'source path differs'):
                        compiler.compile_sources(Path(temporary),{name:'class Thing {}'},[],Path(temporary)/'out')
                    run.assert_not_called()


class GroovyLanguageReceiptTests(unittest.TestCase):
    def test_guest_class_fallback_witnesses_require_native_effect_and_guarded_refusal(self):
        baseline=conformance.cases()[0]
        value={'executionCompleted':True,'cleanObservation':True,'materials':[{'color':0x99ccbb}],
               'registeredMaterials':605,'phase':'FROZEN','candidateAdmissionViolations':[]}
        envelope={'schema':'axiom.result.v1','kernelIsolation':True,'namespaceIsolation':True,
                  'minecraftLaunched':False,'groovyExecutionQualified':False,
                  'result':{'nativeClassSpaceClosed':True,'execution':value}}
        runs={'guarded':deepcopy(envelope)}
        for case in conformance.guest_resolution_cases():
            self.assertEqual(baseline['files'].keys(),case['files'].keys())
            self.assertEqual(conformance.identity(case['files']),case['candidateIdentity'])
            self.assertEqual([conformance.EDITS],[name for name in case['files'] if case['files'][name]!=baseline['files'][name]])
            for suffix in (':reference',':guarded'):
                runs[case['name']+suffix]=deepcopy(envelope)
                observed=runs[case['name']+suffix]['result']['execution']
                if suffix==':reference':observed['materials'][0]['color']=0x010203
                else:observed.update(cleanObservation=False,
                    candidateAdmissionViolations=['candidate.dispatch: invoke classes.MaterialEdits#forName'])
        self.assertEqual(3,len(conformance.check_guest_resolution_observations(runs)))
        first=conformance.guest_resolution_cases()[0]['name']
        for suffix,key,changed_value in ((':reference','materials',[{'color':0x99ccbb}]),
                                        (':guarded','candidateAdmissionViolations',[]),
                                        (':guarded','materials',[{'color':0x010203}]),
                                        (':guarded','cleanObservation',True)):
            changed=deepcopy(runs);changed[first+suffix]['result']['execution'][key]=changed_value
            with self.assertRaises(ValueError):conformance.check_guest_resolution_observations(changed)

    def test_caught_resource_witnesses_keep_entire_program_and_require_sticky_observation(self):
        baseline=conformance.cases()[0];runs={}
        for case in conformance.resource_cases():
            self.assertEqual(baseline['files'].keys(),case['files'].keys())
            self.assertEqual(conformance.identity(case['files']),case['candidateIdentity'])
            for suffix in (':reference',':guarded'):
                runs[case['name']+suffix]={'schema':'axiom.result.v1','kernelIsolation':True,'namespaceIsolation':True,
                    'minecraftLaunched':False,'groovyExecutionQualified':False,
                    'result':{'nativeClassSpaceClosed':True,'execution':{'executionCompleted':True,
                              'candidateResourceFailure':suffix==':guarded','cleanObservation':suffix==':reference',
                              'candidateResourceCause':['java.lang.OutOfMemoryError' if 'heap' in case['name'] else 'java.lang.StackOverflowError'],
                              'materials':['native state'],'registeredMaterials':605,'phase':'FROZEN'}}}
        self.assertEqual(2,len(conformance.check_resource_observations(runs)))
        first=conformance.resource_cases()[0]['name']+':guarded'
        for key,value in (('candidateResourceFailure',False),('cleanObservation',True),('executionCompleted',False),('materials',[])):
            changed=deepcopy(runs);changed[first]['result']['execution'][key]=value
            with self.assertRaises(ValueError):conformance.check_resource_observations(changed)
        stack='caught-stack-exhaustion:guarded';changed=deepcopy(runs)
        observed=changed[stack]['result']['execution']
        observed.update(candidateResourceFailure=False,candidateResourceCause=[],candidateLinkageFailure=True,
                        candidateCaughtCause=['java.lang.NoClassDefFoundError','java.lang.ClassNotFoundException'])
        self.assertEqual(2,len(conformance.check_resource_observations(changed)))
        observed['candidateCaughtCause']=[]
        with self.assertRaises(ValueError):conformance.check_resource_observations(changed)

    def test_failed_discovery_is_not_a_successful_cli_exit(self):
        argv=[]
        for name in ('java-home','engine-home','images','library-root','api-program','language-program',
                     'cleanroom','groovyscript','gtceu','report'):
            argv.extend(['--'+name,'placeholder'])
        for failure,code in ((None,0),('Observed mechanism differs',1)):
            with patch.object(conformance,'qualify',return_value={'status':'observed-not-qualified','runs':{},
                              'mechanismChecks':[],'mechanismCheckFailure':failure}),patch('builtins.print'):
                self.assertEqual(code,conformance.main(argv))

    def test_authoring_witnesses_preserve_complete_program_and_expose_native_state(self):
        baseline=conformance.cases()[0]
        authors=conformance.authoring_cases()
        self.assertEqual(4,len(authors))
        self.assertEqual(4,len({row['name'] for row in authors}))
        for row in authors:
            self.assertEqual(baseline['files'].keys(),row['files'].keys())
            self.assertEqual(conformance.identity(row['files']),row['candidateIdentity'])
            self.assertEqual([conformance.EDITS],[name for name in row['files'] if row['files'][name]!=baseline['files'][name]])
            self.assertIsInstance(row['expectedColor'],int)
        self.assertIn(b'step(1) + step(2) + step(3)',authors[0]['files'][conformance.EDITS])
        self.assertEqual(123,authors[0]['expectedColor'])

    def test_authoring_comparison_requires_native_positive_and_unchanged_guarded_results(self):
        runs={}
        for case in conformance.authoring_cases():
            value={'cleanObservation':True,'materials':[{'color':case['expectedColor']}],
                   'candidateAdmissionViolations':[],'nativeErrors':[],'coverageGaps':[],
                   'registeredMaterials':605,'phase':'FROZEN','scriptIndex':[],'effectiveSide':'SERVER','physicalSide':'SERVER'}
            value['candidateDispatchObservations']={'getProperty it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap#first':1}
            for suffix in (':reference',':guarded'):
                runs[case['name']+suffix]={'schema':'axiom.result.v1','kernelIsolation':True,'namespaceIsolation':True,
                    'minecraftLaunched':False,'groovyExecutionQualified':False,
                    'result':{'nativeClassSpaceClosed':True,'execution':deepcopy(value)}}
        self.assertEqual(4,len(conformance.check_authoring_observations(runs)))
        first=conformance.authoring_cases()[0]['name']
        for suffix,key,value in ((':reference','materials',[{'color':0}]),(':guarded','phase','CLOSED'),
                                 (':guarded','candidateAdmissionViolations',['caught']),(':guarded','cleanObservation',False)):
            changed=deepcopy(runs);changed[first+suffix]['result']['execution'][key]=value
            with self.assertRaises(ValueError):conformance.check_authoring_observations(changed)

    def test_structural_witness_requires_parser_only_isolated_envelope(self):
        record={'schema':'axiom.result.v1','kernelIsolation':True,'namespaceIsolation':True,
                'groovyExecutionQualified':False,'minecraftLaunched':False,
                'result':{'candidateCompilationStarted':False,'nativeClassSpaceCreated':False,
                          'sourceAdmission':{'candidateCodeGenerated':False,'runtimeAdmissionQualified':False,
                                             'structurallyAdmitted':False,'findings':[{'code':'admission.annotation'}]}}}
        conformance.structure_observation({'test':record},'test','admission.annotation')
        for path in (('kernelIsolation',),('namespaceIsolation',),('groovyExecutionQualified',),
                     ('result','candidateCompilationStarted'),('result','nativeClassSpaceCreated'),
                     ('result','sourceAdmission','candidateCodeGenerated'),('result','sourceAdmission','runtimeAdmissionQualified')):
            changed=deepcopy(record);target=changed
            for key in path[:-1]:target=target[key]
            target[path[-1]]=not target[path[-1]]
            with self.assertRaises(ValueError):conformance.structure_observation({'test':changed},'test','admission.annotation')

    def test_structure_and_dispatch_witnesses_keep_complete_program_and_exact_identity(self):
        baseline=conformance.cases()[0]
        witnesses=[*conformance.admission_cases(),*conformance.dispatch_cases()]
        self.assertEqual(len(witnesses),len({row['name'] for row in witnesses}))
        for row in witnesses:
            self.assertEqual(baseline['files'].keys(),row['files'].keys())
            self.assertEqual(conformance.identity(row['files']),row['candidateIdentity'])
            changes=[name for name in row['files'] if row['files'][name]!=baseline['files'][name]]
            self.assertEqual([conformance.EDITS],changes)
        self.assertEqual(baseline,conformance.cases()[0])

    def test_additional_language_witnesses_preserve_complete_frozen_g0_corpus(self):
        baseline=conformance.cases()
        mechanisms=conformance.language_cases()
        self.assertEqual(3,len(mechanisms))
        self.assertEqual(3,len({row['name'] for row in mechanisms}))
        for case in mechanisms:
            self.assertEqual(set(baseline[0]['files']),set(case['files']))
            self.assertEqual(case['candidateIdentity'],conformance.identity(case['files']))
            changed=[name for name,raw in case['files'].items() if baseline[0]['files'][name]!=raw]
            self.assertEqual([conformance.EDITS],changed)
        self.assertEqual(baseline,conformance.cases())

    def test_fresh_rebuild_comparison_rejects_each_semantic_identity_change(self):
        keys=('schema','revisions','sourceInputs','sources','classes','artifacts','images','libraries','runtimeInputs',
              'mappingsSha256','compilerView')
        original={key:{'checked':'bytes'} for key in keys}
        conformance.compare_program(original,deepcopy(original))
        for key in keys:
            with self.subTest(key=key):
                changed=deepcopy(original);changed[key]={'tampered':True}
                with self.assertRaisesRegex(ValueError,'exact-source rebuild'):
                    conformance.compare_program(changed,original)

    def test_rebuild_comparison_allows_new_custody_record_not_changed_program(self):
        keys=('schema','revisions','sourceInputs','sources','classes','artifacts','images','libraries','runtimeInputs',
              'mappingsSha256','compilerView')
        original={key:{} for key in keys};old={**original,'recipeInputs':{'old-input':'old-hash'}}
        conformance.compare_program(old,{**original,'recipeInputs':{'current-input':'current-hash'}})

    def test_execution_envelope_requires_isolation_and_scope(self):
        original={'schema':'axiom.result.v1','kernelIsolation':True,'namespaceIsolation':True,
                  'minecraftLaunched':False,'groovyExecutionQualified':False,
                  'result':{'nativeClassSpaceClosed':True,'execution':{'example':'test-only'}}}
        self.assertEqual({'example':'test-only'},conformance.execution({'case':original},'case'))
        for key in ('schema','kernelIsolation','namespaceIsolation','minecraftLaunched','groovyExecutionQualified'):
            with self.subTest(key=key):
                changed=deepcopy(original);changed.pop(key)
                with self.assertRaises(ValueError):conformance.execution({'case':changed},'case')
        changed=deepcopy(original);changed['result']['failure']='native failure'
        with self.assertRaises(ValueError):conformance.execution({'case':changed},'case')


if __name__=='__main__':unittest.main()
