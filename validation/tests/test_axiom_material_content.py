"""Original-identity content assembly contracts; execution evidence is separate."""
from pathlib import Path
from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import axiom_material_api_sources as api
import axiom_material_content_sources as content
import axiom_groovy_language_conformance as conformance
import build_axiom_material_api as builder
from axiom_material_program_cases import cases, identity


class NativeMaterialContentTests(unittest.TestCase):
    def test_native_content_is_part_of_same_api_build(self):
        self.assertEqual(len(content.CLASSES), len(set(content.CLASSES)))
        self.assertTrue(set(content.CLASSES + content.SUPPORT).issubset(api.SUPPORT))
        for name in ('api/unification/OreDictUnifier', 'api/unification/stack/UnificationEntry',
                     'api/items/materialitem/MetaPrefixItem', 'api/items/metaitem/MetaItem'):
            self.assertIn(name, content.CLASSES)
        text = (ROOT / 'tools/axiom_material_content_sources.py').read_text()
        for forbidden in ('FluidMaterial', 'FluidEnvironment', 'MaterialState', 'PrefixDependencies'):
            self.assertNotIn(forbidden, text)

    def test_only_native_registry_receiver_is_remapped(self):
        mappings = '\n'.join((
            'MD: net/minecraft/util/registry/RegistrySimple/func_lookup (I)I net/minecraft/util/registry/RegistrySimple/getObject (I)I',
            'MD: net/minecraft/util/registry/RegistryNamespaced/func_id (I)I net/minecraft/util/registry/RegistryNamespaced/getObjectById (I)I',
            'MD: net/minecraft/util/registry/RegistryNamespaced/func_index (I)I net/minecraft/util/registry/RegistryNamespaced/getIDForObject (I)I'))
        text = 'registry.getObject(name); registry.getObjectById(id); registry.getIDForObject(material); unrelated.getObject(name);'
        actual = content.bind_registry(text, mappings)
        self.assertEqual('registry.func_lookup(name); registry.func_id(id); registry.func_index(material); unrelated.getObject(name);', actual)
        ambiguous = mappings + '\nMD: net/minecraft/util/registry/RegistrySimple/other (I)I net/minecraft/util/registry/RegistrySimple/getObject (I)I'
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            content.bind_registry(text, ambiguous)

    def test_stack_copy_is_not_material_stack_copy(self):
        text = ('event.getOre().copy(); itemStacks.get(0).copy(); ItemStack::copy; '
                'materialInfo.getMaterial().copy(); itemStack.isEmpty(); entries.isEmpty();')
        with patch.object(content.unifier, 'validate_bindings'), patch.object(content, 'bind_registry', side_effect=lambda t, m: t):
            actual = content.bind_unifier(text, '')
        self.assertEqual(3, actual.count('func_77946_l'))
        self.assertIn('materialInfo.getMaterial().copy()', actual)
        self.assertIn('itemStack.func_190926_b()', actual)
        self.assertIn('entries.isEmpty()', actual)

    def test_content_witnesses_are_whole_program_edits(self):
        original = cases()[0]
        variants = conformance.content_cases()
        self.assertEqual(['post-material-plate', 'without-ore-property', 'post-material-compressed', 'post-material-frame',
                          'post-material-disable-ore'],
                         [c['name'] for c in variants])
        for case in variants:
            self.assertEqual(set(original['files']), set(case['files']))
            self.assertEqual(1, sum(raw != original['files'][name] for name, raw in case['files'].items()))
            self.assertNotEqual(original['candidateIdentity'], case['candidateIdentity'])
            self.assertEqual(identity(case['files']), case['candidateIdentity'])

    def test_lifecycle_uses_original_objects_and_native_event_callback(self):
        text = (ROOT / 'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeMaterialContent.java').read_text()
        for fragment in ('RegistryEvent.Register<Item>', 'ForgeRegistries.ITEMS',
                         'item.getMaterial(stack) == material', 'entry.material == material',
                         'phase = Phase.FAILED', 'MinecraftForge.EVENT_BUS.unregister(listener)',
                         '"selected", stack(OreDictUnifier.get(prefix, material)', '"generated", generated'):
            self.assertIn(fragment, text)
        self.assertLess(text.index('OreDictUnifier.init()'), text.index('NativePrefixItemDeclarations.construct()'))
        self.assertLess(text.index('NativePrefixItemDeclarations.construct()'), text.index('NativePrefixItemDeclarations.registerOres()'))
        for forbidden in ('.clear()', 'runMaterialHandlers()', 'new Material(', 'FluidMaterial'):
            self.assertNotIn(forbidden, text)

    def test_native_block_binding_preserves_gt_material_lookup_owner(self):
        names=('setTranslationKey','setCreativeTab','getTranslationKey','getMaterial','getPushReaction','isOpaqueCube',
               'onEntityCollision','onBlockActivated','getCollisionBoundingBox','getRenderLayer','getBlockFaceShape','getMapColor','addInformation')
        methods={('net/minecraft/block/Block',name):{'native_'+name} for name in names}
        text=('Material a = GregTechAPI.materialManager.getMaterial(materialName); '
              'return super.getMaterial(state); String getName(Material material) { return material.getName(); } '
              'entry.getValue(); state.getValue(property);')
        with patch.object(content.blocks,'validate_bindings'), patch.object(content.blocks.items,'native_symbols',return_value=({},methods)):
            actual=content.blocks.bind_native_symbols('PropertyMaterial',text,'mapping')
        self.assertIn('GregTechAPI.materialManager.getMaterial(materialName)',actual)
        self.assertIn('super.native_getMaterial(state)',actual)
        self.assertIn('String func_177702_a(Material material)',actual)
        self.assertIn('material.getName()',actual)
        self.assertIn('entry.getValue()',actual)
        self.assertIn('state.func_177229_b(property)',actual)

    def test_native_access_and_generation_sources_are_custody_inputs(self):
        frozen=builder.recipe_inputs()
        for path in ('tools/axiom_material_access.py','tools/axiom_material_program_ore_sources.py',
                     'modules/axiom/jvm/src/main/java/research/orthrus/axiom/NativeMaterialAccess.java',
                     'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeMaterialBlockAccess.java',
                     'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeMaterialContent.java'):
            self.assertEqual((ROOT/path).read_bytes(),frozen[ROOT/path])
        self.assertIn('src/main/resources/gregtech_at.cfg',api.RESOURCES)
        self.assertNotIn('SusyStoneTypes',content.ores.PATHS)
        self.assertIn('StoneTypes',content.ores.PATHS)

    def test_ore_generation_occurs_in_block_event_before_native_registration(self):
        text=(ROOT/'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeMaterialContent.java').read_text()
        start=text.index('public final class BlockRegistration')
        end=text.index('public final class Registration',start)
        callback=text[start:end]
        self.assertLess(callback.index('NativeOreDeclarations.generate()'),callback.index('NativeMaterialBlockDeclarations.registerBlocks('))
        self.assertLess(callback.index('NativeMaterialBlockDeclarations.registerBlocks('),callback.index('NativeOreDeclarations.registerBlocks('))
        for fragment in ('"ordinaryDrop", stack(dropped', '"uniqueDrop", stone.shouldBeDroppedAsItem',
                         '"worldGenerationQualified", false', '"harvestingEventsQualified", false'):
            self.assertIn(fragment,text)

    @staticmethod
    def native_outcome_records():
        """Checker contract fixtures, not native execution evidence."""
        runs={}
        for name in conformance.NATIVE_OUTCOMES:
            value={'cleanObservation':False,'candidateAdmissionViolations':[], 'registeredMaterials':605,
                   'phase':'FROZEN','executionCompleted':True,'prefixItems':{'phase':'COMPLETE'},
                   'materials':[{'color':0x88bbaa},{'formula':'(Li,Na)AlPO₄(F,OH)'}],
                   'lateRegistered':False,'nativeMessages':[],'nativeErrors':[],'log':''}
            if name=='logged-components-error':
                value.update(log='Tried to use old method for material components',nativeErrors=['Error creating GregTech material'])
            elif name=='property-setter-error':
                value.update(phase='CLOSED',executionCompleted=False,nativeException='Harvest Level must be greater than zero!')
                del value['prefixItems']
            else:
                value['nativeMessages']=[{'level':'ERROR','message':'Materials cannot be registered in the PostMaterialEvent'}]
            envelope={'schema':'axiom.result.v1','kernelIsolation':True,'namespaceIsolation':True,
                      'minecraftLaunched':False,'groovyExecutionQualified':False,
                      'result':{'nativeClassSpaceClosed':True,'execution':value}}
            for mode in ('reference','guarded'):runs['native-outcome:'+name+':'+mode]=deepcopy(envelope)
        return runs

    def test_native_error_checker_keeps_groovy_and_log4j_channels_distinct(self):
        runs=self.native_outcome_records()
        self.assertEqual(3,len(conformance.check_native_outcomes(runs)))
        runs['native-outcome:logged-components-error:guarded']['result']['execution']['nativeErrors']=[]
        with self.assertRaisesRegex(ValueError,'not retained'):
            conformance.check_native_outcomes(runs)

    def test_partial_state_cannot_be_promoted_to_completed_content(self):
        for mutate in (lambda v:v.update(executionCompleted=True),lambda v:v.update(prefixItems={'phase':'COMPLETE'}),
                       lambda v:v['materials'][1].update(formula='rolled-back')):
            runs=self.native_outcome_records()
            for mode in ('reference','guarded'):
                mutate(runs['native-outcome:property-setter-error:'+mode]['result']['execution'])
            with self.assertRaisesRegex(ValueError,'partial material state'):
                conformance.check_native_outcomes(runs)


if __name__ == '__main__': unittest.main()
