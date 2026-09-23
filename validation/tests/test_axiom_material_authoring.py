"""Source-bound ordinary-authoring selection and evidence checks, not native parity."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import axiom_material_authoring_cases as authoring
from axiom_material_program_cases import cases, identity
from build_axiom_material_runtime import verify_method_mappings


class MaterialAuthoringTests(unittest.TestCase):
    def test_reference_compiler_view_precedes_native_classes_without_changing_runtime_inputs(self):
        from axiom_material_observation_conformance import reference_compiler_inputs
        api=Path('api');runtime=[Path('native.jar'),api/'material-api.jar']
        before=list(runtime);annotations=Path('annotations.jar');host=Path('host.jar')
        self.assertEqual([api/'compiler-view.jar',*runtime,annotations,host],
                         reference_compiler_inputs(api,runtime,annotations,host))
        self.assertEqual(before,runtime)
        self.assertNotIn(api/'compiler-view.jar',runtime)

    def test_trait_programs_keep_native_composition_and_the_whole_pack_witness(self):
        raw=b'package globals\nclass Sintering { trait Plasma {} }\n'
        selected=authoring.trait_corpus(raw);base=cases()[0]['files']
        self.assertEqual(11,len(selected))
        for case in selected:
            self.assertEqual(identity(case['files']),case['candidateIdentity'])
            self.assertEqual(set(base)|({'groovy/globals/Sintering.groovy'} if case['name']=='trait-pack-sintering' else set()),set(case['files']))
        self.assertEqual(raw,selected[-1]['files']['groovy/globals/Sintering.groovy'])
        names={case['name'] for case in selected}
        self.assertTrue({'trait-loader-distinct-compositions','trait-native-material-error','trait-native-material-correction'}<=names)

    def test_trait_evidence_compares_original_bytes_and_actual_loader_scopes(self):
        from axiom_material_observation_conformance import check_trait_compilation
        shape={'classVersion':69,'superName':'java/lang/Object','interfaces':['fixture/Marker']}
        source={**shape,'sha256':'source'}
        generated={**shape,'sha256':'adapter'}
        reference={'compilerBytecode':{'fixture.Marker':source},'traitDefinitions':[
            {'name':'fixture.Marker$TraitAdapter','definitionScope':i,'bytecode':generated,'methodOrderIndependentSha256':'canonical'} for i in (1,2)]}
        native={'traitCompilations':{'fixture.Marker':{**shape,'originalSha256':'source','guardedSha256':'guarded'}},
            'traitDefinitions':[{**shape,'name':'fixture.Marker$TraitAdapter','definitionScope':i,
                'kind':'adapter','originalSha256':'adapter','guardedSha256':'guarded','methodOrderIndependentSha256':'canonical'} for i in (1,2)]}
        self.assertEqual([],check_trait_compilation(reference,native,['fixture.Marker']))
        for key,value in [('definitionScope',1),('originalSha256','different'),('interfaces',[]),('methodOrderIndependentSha256','different')]:
            changed=deepcopy(native);changed['traitDefinitions'][1][key]=value
            self.assertTrue(check_trait_compilation(reference,changed,['fixture.Marker']),key)
        changed=deepcopy(native);changed['traitCompilations']['fixture.Marker']['originalSha256']='different'
        self.assertTrue(check_trait_compilation(reference,changed,['fixture.Marker']))

    def test_record_programs_preserve_native_declarations_and_whole_lifecycle(self):
        selected=authoring.record_corpus();base=cases()[0]['files']
        self.assertEqual(11,len(selected))
        for case in selected:
            self.assertEqual(set(base),set(case['files']))
            self.assertIn(b'record Reagent(',case['files']['groovy/classes/MaterialEdits.groovy'])
            self.assertEqual(identity(case['files']),case['candidateIdentity'])
            for path,raw in base.items():
                if path!='groovy/classes/MaterialEdits.groovy':self.assertEqual(raw,case['files'][path])

    def test_record_compiler_evidence_requires_exact_original_bytes(self):
        from axiom_material_observation_conformance import check_record_compilation
        reference={'compilerTargetBytecode':'25','compilerBytecode':{'classes.Reagent':{
            'superName':'java/lang/Record','classVersion':69,'sha256':'original','recordComponents':[{'name':'amount','descriptor':'I'}]}}}
        native={'recordCompilations':{'classes.Reagent':{'classVersion':69,'originalSha256':'original','guardedSha256':'guarded',
            'components':[{'name':'amount','descriptor':'I'}]}},
            'candidateDispatchObservations':{'compiler.constant AnnotationCollectorMode#PREFER_EXPLICIT_MERGED':1}}
        self.assertEqual([],check_record_compilation(reference,native))
        for field,value in [('classVersion',52),('originalSha256','different'),('components',[])]:
            changed=deepcopy(native);changed['recordCompilations']['classes.Reagent'][field]=value
            self.assertTrue(check_record_compilation(reference,changed),field)

    def test_native_language_programs_keep_the_complete_lifecycle_and_transforms(self):
        selected=authoring.language_corpus();base=cases()[0]
        self.assertEqual(4,len(selected))
        self.assertEqual([65,7,10,0x99ccbb],[case['expectedColor'] for case in selected])
        for case in selected:
            self.assertEqual(base['files'].keys(),case['files'].keys())
            self.assertEqual(base['files']['groovy/runConfig.json'],case['files']['groovy/runConfig.json'])
            self.assertEqual(identity(case['files']),case['candidateIdentity'])
            self.assertIn(b'@groovy.transform.TupleConstructor',case['files']['groovy/classes/MaterialEdits.groovy'])
            for path,raw in base['files'].items():
                if path!='groovy/classes/MaterialEdits.groovy':self.assertEqual(raw,case['files'][path])

    def test_whole_program_selection_never_enters_security_corpus(self):
        selected=authoring.corpus();base=cases()[0]
        self.assertEqual(9,len(selected))
        self.assertEqual(base['files'],selected[0]['files'])
        self.assertEqual({'complete-program','authoring-helper-order','authoring-collections','authoring-loop',
            'authoring-safe-navigation','authoring-default-import','authoring-native-resource-mapping',
            'authoring-nested-binding','authoring-string-builder-expansion'}, {case['name'] for case in selected})
        for case in selected:
            self.assertEqual(base['files'].keys(),case['files'].keys())
            self.assertEqual(base['files']['groovy/runConfig.json'],case['files']['groovy/runConfig.json'])
            self.assertEqual(identity(case['files']),case['candidateIdentity'])

    def test_reference_and_installed_must_both_observe_native_result(self):
        case=authoring.corpus()[1]
        reference={'phase':'FROZEN','registeredMaterials':605,'executionCompleted':True,'coverageGaps':[],
            'nativeErrors':[],'materials':[{'color':case['expectedColor']},{},{}]}
        native={**deepcopy(reference),'cleanObservation':True,'candidateAdmissionViolations':[]}
        self.assertEqual([],authoring.check(case,reference,native))
        for side in ('reference','installed'):
            for key,value in (('phase','CLOSED'),('registeredMaterials',602),('executionCompleted',False),
                              ('nativeErrors',['native error']),('coverageGaps',['unknown']),('materials',[])):
                before,after=deepcopy(reference),deepcopy(native)
                (before if side=='reference' else after)[key]=value
                self.assertTrue(authoring.check(case,before,after),(side,key))

    def test_native_expansion_requires_actual_generated_plate_not_flag_only(self):
        case=next(c for c in authoring.corpus() if c.get('expectedPlate'))
        reference={'phase':'FROZEN','registeredMaterials':605,'executionCompleted':True,'coverageGaps':[],
            'nativeErrors':[],'materials':[{'color':case['expectedColor']},{},{}]}
        native={**deepcopy(reference),'cleanObservation':True,'candidateAdmissionViolations':[]}
        self.assertTrue(authoring.check(case,reference,native))
        native['prefixItems']={'forms':[{'material':'supersymmetry:developer_aluminosilicate','prefix':'plate','generated':[{'item':'native'}]}]}
        self.assertEqual([],authoring.check(case,reference,native))
        native['prefixItems']['forms'][0]['generated']=[]
        self.assertTrue(authoring.check(case,reference,native))

    def mapping_fixture(self):
        return ({'nativeMethodMappings':{'native.Type':{'getValue':'func_read'}},
                 'nativeMethods':{'native.Type':['func_read()Ljava/lang/String;']}},
                b'MD: native/Type/func_read ()Ljava/lang/String; native/Type/getValue ()Ljava/lang/String;\n')

    def test_native_mapping_binds_exact_source_names_and_descriptor_family(self):
        policy,raw=self.mapping_fixture()
        record=verify_method_mappings(policy,raw)
        self.assertEqual([{'owner':'native.Type','authoringName':'getValue','nativeName':'func_read',
            'descriptors':['func_read()Ljava/lang/String;']}],record['methods'])
        self.assertEqual('assets/groovyscript/mappings.srg',record['source'])

    def test_changed_missing_duplicate_or_partial_mapping_is_not_guessed(self):
        policy,raw=self.mapping_fixture()
        for changed in (b'',raw+raw,raw.replace(b'func_read',b'func_other'),
                        raw.replace(b'getValue',b'getOther'),raw.replace(b'()Ljava/lang/String;',b'()I',1)):
            with self.assertRaises(ValueError):verify_method_mappings(policy,changed)
        changed=deepcopy(policy);changed['nativeMethods']['native.Type'].append('func_read(I)Ljava/lang/String;')
        with self.assertRaises(ValueError):verify_method_mappings(changed,raw)


if __name__=='__main__':unittest.main()
