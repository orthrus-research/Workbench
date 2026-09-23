"""Witness/custody contracts; actual native execution is the separate lane."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tools'))
import axiom_pack_meta_item_conformance as items
import build_axiom_material_api as api
import build_axiom_material_runtime as runtime


class MetaItemWitnessTests(unittest.TestCase):
    def test_complete_programs_change_only_the_item_edit_and_repeat_fresh_baseline(self):
        cases=items.corpus(b'selected config')
        self.assertEqual(24,len(cases));self.assertEqual(cases[1]['files'],cases[-1]['files'])
        self.assertEqual(len(cases),len({row['name'] for row in cases}))
        for case in cases:
            self.assertEqual(set(cases[0]['files']),set(case['files']))
            for path,raw in case['files'].items():
                if path!=items.SCRIPT:self.assertEqual(cases[0]['files'][path],raw)
        self.assertEqual(10,items.program("addItem(1, 'probe')").decode().splitlines().index("        addItem(1, 'probe')")+1)

    def test_duplicates_ranges_and_unchecked_values_are_native_outcomes_not_added_rules(self):
        cases={row['name']:row['expected'] for row in items.corpus(b'config')}
        self.assertEqual([False,True],cases['duplicate-name']['lookups'])
        self.assertNotIn('error',cases['duplicate-name'])
        self.assertIn('error',cases['duplicate-id'])
        self.assertNotIn('error',cases['stack-native-unchecked-upper'])
        self.assertNotIn('error',cases['electric-native-unchecked-values'])
        self.assertTrue(cases['unadmitted-delegate']['admission'])
        self.assertEqual([{'class':None},{'class':None}],cases['native-null-components']['components'])
        self.assertIsNone(cases['native-null-bauble-type']['components'][0]['baubleType'])

    def test_native_meta_owners_and_nested_classes_cannot_be_source_replacements(self):
        for owner in ('MetaItem','MetaItem$MetaValueItem','StandardMetaItem'):
            with self.assertRaisesRegex(ValueError,'remain unchanged'):
                api.check_native_overrides(['gregtech/api/items/metaitem/'+owner+'.class'])
        api.check_native_overrides(['gregtech/api/items/materialitem/MetaPrefixItem.class'])

    def test_compiler_view_does_not_enter_runtime_dependency_or_classpath_lists(self):
        text=(ROOT/'tools/build_axiom_material_runtime.py').read_text()
        self.assertIn("[api / 'compiler-view.jar', *dependencies]",text)
        self.assertNotIn("dependencies.append(api / 'compiler-view.jar')",text)
        self.assertNotIn("dependencies += [api / 'compiler-view.jar'",text)
        frozen=runtime.host_sources()
        self.assertIn(runtime.GATES/'MaterialDiagnosticGroups.java',frozen)
        self.assertIn(runtime.NATIVE/'research/orthrus/axiom/materialhost/NativeMetaItemObservations.java',frozen)

    def test_observer_never_creates_stacks_or_registers_items(self):
        text=(runtime.NATIVE/'research/orthrus/axiom/materialhost/NativeMetaItemObservations.java').read_text()
        self.assertIn('MaterialCallGate.observedInstance',text)
        self.assertNotIn('getStackForm(',text);self.assertNotIn('.register(',text)
        self.assertNotIn('new ItemStack(',text)

    def test_native_early_host_freezes_its_launch_prefix_source(self):
        frozen=runtime.host_sources()
        host=runtime.NATIVE/'research/orthrus/axiom/materialhost/NativeEarlyClassSpace.java'
        prefix=runtime.GATES/'NativeEarlyLaunchPrefix.java'
        self.assertIn(host,frozen)
        self.assertIn(prefix,frozen)
        self.assertEqual(prefix.read_bytes(),frozen[prefix])

    def test_returned_event_alone_cannot_pass_complete_pack_item_check(self):
        self.assertTrue(items.check_pack_items({'phase':'FROZEN','log':'Finished adding metaitems'}))


if __name__=='__main__':unittest.main()
