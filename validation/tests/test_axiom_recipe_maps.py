"""Recipe input custody and evidence checks; actual semantics use fresh JVMs."""
from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import axiom_recipe_native_inputs as inputs
import axiom_recipe_map_cases as cases
import axiom_pack_helper_cases as helpers
from axiom_material_program_cases import identity


class NativeRecipeInputsTests(unittest.TestCase):
    def test_gcym_selection_retains_both_original_event_injections(self):
        path='mixins.susy.gcym.json'
        source={'package':'supersymmetry.mixins.gcym','refmap':'mixins.susy.refmap.json',
                'mixins':['GCYMCommonProxyMixin','GCYMEventHandlersMixin','GCYMMetaTileEntitiesMixin']}
        mixin='supersymmetry/mixins/gcym/GCYMEventHandlersMixin.class'
        resources={path:json.dumps(source).encode(),source['refmap']:b'{}',mixin:b'original complete injections'}
        raw,receipt=inputs.pack_gcym_mixins(resources.__getitem__)
        self.assertEqual(['GCYMEventHandlersMixin'],json.loads(raw)['mixins'])
        self.assertTrue(json.loads(raw)['required'])
        self.assertEqual(['GCYMCommonProxyMixin','GCYMMetaTileEntitiesMixin'],receipt['excluded'])
        self.assertEqual(sha256(resources[mixin]).hexdigest(),receipt['selected'][mixin])
        self.assertFalse(receipt['nativePluginDiscoveryExecuted'])
        self.assertFalse(receipt['wholeInstalledMixinSet'])
        for change in ({'package':'other'}, {'refmap':'other'}, {'mixins':[]},
                       {'mixins':['GCYMEventHandlersMixin']*2}, {'plugin':'unknown'},
                       {'client':[]}, {'server':[]}):
            with self.assertRaisesRegex(ValueError,'Unexpected selected'):
                inputs.pack_gcym_mixins({**resources,path:json.dumps({**source,**change}).encode()}.__getitem__)

    def test_recipe_catalog_mixin_keeps_original_hook_and_excludes_client_execution(self):
        path='mixins.susy.gregtech.json'
        source={'package':'supersymmetry.mixins.gregtech','refmap':'mixins.susy.refmap.json',
                'mixins':['RecipeMapsMixin','BlockMachineMixin'],'client':['PipeRendererMixin']}
        mixin='supersymmetry/mixins/gregtech/RecipeMapsMixin.class'
        resources={path:json.dumps(source).encode(),source['refmap']:b'{}',mixin:b'original mixin'}
        raw,receipt=inputs.pack_recipe_mixins(resources.__getitem__)
        self.assertEqual(['RecipeMapsMixin'],json.loads(raw)['mixins'])
        self.assertEqual([],json.loads(raw)['client'])
        self.assertEqual(['BlockMachineMixin'],receipt['excluded'])
        self.assertEqual(['PipeRendererMixin'],receipt['excludedClient'])
        self.assertEqual(sha256(resources[mixin]).hexdigest(),receipt['selected'][mixin])
        self.assertFalse(receipt['wholeInstalledMixinSet'])
        for change in ({'mixins':[]},{'mixins':['RecipeMapsMixin','RecipeMapsMixin']},{'plugin':'unselected.Plugin'}):
            with self.assertRaisesRegex(ValueError,'Unexpected selected'):
                inputs.pack_recipe_mixins({**resources,path:json.dumps({**source,**change}).encode()}.__getitem__)

    def fixture(self, root):
        descriptors={}
        for key, prefixes in inputs.ARTIFACTS.items():
            name=key+'.jar'
            with zipfile.ZipFile(root/name,'w') as archive:
                archive.writestr(prefixes[0]+'Marker.class',b'unchanged native bytes')
                archive.writestr('unselected/NotAdmitted.class',b'not selected')
                if key=='gregtech-ce-unofficial':
                    archive.writestr('gregtech/api/recipes/RecipeMap.class',b'original RecipeMap bytes')
                for resource in inputs.RESOURCES.get(key, ()):
                    archive.writestr(resource,b'unchanged original resource')
            digest=sha1((root/name).read_bytes()).hexdigest()
            descriptors['mods/'+key+'.pw.toml']=(f'filename = "{name}"\n[download]\nhash-format = "sha1"\nhash = "{digest}"\n').encode()
        return descriptors

    def test_pack_git_pin_and_every_original_class_byte_are_bound(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);descriptors=self.fixture(root)
            def read(pack, command, ref):
                self.assertEqual('show',command)
                self.assertTrue(ref.startswith(inputs.selected_revisions()['supersymmetry']+':'))
                return descriptors[ref.split(':',1)[1]]
            with patch.object(inputs,'git',side_effect=read):
                classes, manifest=inputs.read_inputs(Path('pack'),root)
            self.assertEqual(b'original RecipeMap bytes',classes['gregtech/api/recipes/RecipeMap.class'])
            self.assertNotIn('unselected/NotAdmitted.class',classes)
            self.assertEqual(len(inputs.ARTIFACTS),len(manifest['artifacts']))
            self.assertEqual(set(classes)-{'axiom-native-recipe-optionals.txt'},set(manifest['classes'])|set(manifest['resources']))
            for resource, row in manifest['resources'].items():
                self.assertEqual(b'unchanged original resource',classes[resource])
                self.assertEqual(sha256(classes[resource]).hexdigest(),row['sha256'])
            self.assertFalse(manifest['apiAdmission'])
            self.assertEqual('local-inputs-only',manifest['distribution'])

    def test_changed_artifact_and_indirect_artifact_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);descriptors=self.fixture(root)
            with patch.object(inputs,'git',side_effect=lambda p,c,r:descriptors[r.split(':',1)[1]]):
                path=root/'gregtech-ce-unofficial.jar';saved=path.read_bytes()
                path.write_bytes(saved+b'drift')
                with self.assertRaisesRegex(ValueError,'hash differs'):inputs.read_inputs(Path('pack'),root)
                target=root/'saved.jar';target.write_bytes(saved);path.unlink();path.symlink_to(target)
                with self.assertRaisesRegex(ValueError,'regular pack artifact'):inputs.read_inputs(Path('pack'),root)

    def test_pack_mixin_selection_retains_native_code_and_explicit_exclusions(self):
        path='mixins.supercritical.gregtech.json'
        source={'package':'supercritical.mixins.gregtech','refmap':'mixins.supercritical.refmap.json',
                'target':'@env(DEFAULT)','compatibilityLevel':'JAVA_8','mixins':['MixinElement','MixinOrePrefix','MixinMaterial']}
        mixin='supercritical/mixins/gregtech/MixinElement.class'
        prefix='supercritical/mixins/gregtech/MixinOrePrefix.class'
        resources={path:json.dumps(source).encode(),source['refmap']:b'{"mappings":{},"data":{}}',
                   mixin:b'original bytecode',prefix:b'original prefix bytecode'}
        raw,receipt=inputs.pack_material_mixins(resources.__getitem__)
        selected=json.loads(raw)
        self.assertEqual(['MixinElement','MixinOrePrefix'],selected['mixins'])
        self.assertTrue(selected['required'])
        self.assertEqual(['MixinMaterial'],receipt['excluded'])
        self.assertEqual(sha256(resources[mixin]).hexdigest(),receipt['selected'][mixin])
        self.assertEqual(sha256(resources[prefix]).hexdigest(),receipt['selected'][prefix])
        self.assertFalse(receipt['wholeInstalledMixinSet'])
        for key,value in [('package','other'),('refmap','other'),('mixins',[]),('mixins',['MixinElement']),
                          ('mixins',['MixinElement','MixinOrePrefix','MixinOrePrefix']),('plugin','unselected.Plugin')]:
            changed={**resources,path:json.dumps({**source,key:value}).encode()}
            with self.assertRaisesRegex(ValueError,'Unexpected selected'):
                inputs.pack_material_mixins(changed.__getitem__)


class NativeRecipeCorpusTests(unittest.TestCase):
    def test_pack_helper_corpus_retains_complete_source_bytes_in_every_case(self):
        sources={path:('// complete '+path+'\r\n').encode() for path in helpers.PATHS}
        selected=helpers.corpus(sources)
        self.assertEqual(8,len(selected))
        for case in selected:
            self.assertEqual(identity(case['files']),case['candidateIdentity'])
            for path,raw in sources.items():self.assertEqual(raw,case['files'][path])
        with self.assertRaisesRegex(ValueError,'Complete selected'):helpers.corpus({})

    def test_complete_pack_extension_is_retained_without_rewriting(self):
        raw=b'// original pack bytes\r\n'
        selected=cases.corpus(raw)
        self.assertEqual(11,len(selected))
        for case in selected:
            self.assertEqual(identity(case['files']),case['candidateIdentity'])
            if case.get('removeExtension'):self.assertNotIn(cases.EXTENSION,case['files'])
            else:self.assertEqual(raw,case['files'][cases.EXTENSION])

    def test_limits_and_native_constructor_side_effects_are_both_required(self):
        case=cases.corpus(b'// original')[0]
        row={'name':'axiom_map','limits':[8,9,6,7],'modifiable':[True]*4,'hidden':False,
             'builderLinked':True,'categoryLinked':True,'virtualizedRegistryLinked':True,'nativeClassSpace':True}
        before={'recipeMaps':[row]}
        after={**deepcopy(before),'candidateDispatchObservations':{
            'setProperty org.codehaus.groovy.runtime.HandleMetaClass#modifyMax'+suffix:1
            for suffix in ('Inputs','Outputs','FluidInputs','FluidOutputs')}}
        self.assertEqual([],cases.check(case,before,after))
        for key,value in (('limits',[1,2,3,4]),('builderLinked',False),('categoryLinked',False),
                          ('virtualizedRegistryLinked',False),('nativeClassSpace',False),('modifiable',[False]*4)):
            changed=deepcopy(after);changed['recipeMaps'][0][key]=value
            self.assertTrue(cases.check(case,before,changed),key)


if __name__=='__main__':unittest.main()
