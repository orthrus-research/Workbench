"""Source/evidence contract tests; no native execution or qualification claim."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import axiom_pack_effect_conformance as effects
import axiom_pack_meta_item_conformance as items


class EffectWitnessTests(unittest.TestCase):
    def test_complete_saved_program_preserves_selected_loader_and_configuration_bytes(self):
        run_config=b'{"loaders":{"preInit":["classes/","globals/","material/","preInit/"],"postInit":["prePostInit/","postInit/"]}}'
        rows=effects.corpus(b'native selected config',run_config)
        self.assertEqual(7,len(rows));self.assertEqual(7,len({row['name'] for row in rows}))
        for row in rows:
            self.assertEqual(run_config,row['files'][effects.RUN_CONFIG])
            self.assertEqual(b'native selected config',row['files'][effects.configuration.CONFIG])
            self.assertNotIn('observeMaterials',row);self.assertNotIn('expectations',row)
            self.assertEqual(set(rows[0]['files']),set(row['files']))
            for path,raw in row['files'].items():
                if path!=items.SCRIPT:self.assertEqual(rows[0]['files'][path],raw)
        self.assertEqual(rows[0]['files'],rows[-1]['files'])

    def test_exact_same_count_replacement_and_native_error_source_are_explicit(self):
        rows={row['name']:row for row in effects.corpus(b'config',b'runConfig')}
        self.assertEqual({'added':[effects.OWNER+'#2'],'removed':[effects.OWNER+'#1'],'modified':[]},rows['same-count-identity-replacement']['effectDelta'])
        self.assertEqual([effects.OWNER+'#1'],rows['same-count-property']['effectDelta']['modified'])
        self.assertEqual([],rows['deletion']['expected']['names'])
        error=rows['native-error']['files'][items.SCRIPT].decode().splitlines()
        self.assertIn('setMaxStackSize(0)',error[9])

    def execution(self,variants):
        return {'customMetaItems':{'status':'observed','items':[{'registryName':effects.OWNER,'nativeClassSpace':True,
                 'forgeRegistered':False,'variants':variants}]}}

    def test_same_count_native_field_change_is_not_hidden_by_identity_membership(self):
        old=self.execution([{'meta':1,'name':'probe','maxStackSize':64,'nameLookupIdentity':True}])
        new=deepcopy(old);new['customMetaItems']['items'][0]['variants'][0]['maxStackSize']=16
        self.assertEqual({'added':[],'removed':[],'modified':[effects.OWNER+'#1']},
                         effects.identity_delta(effects.native_variants(old),effects.native_variants(new)))

    def test_missing_catalog_is_not_empty_but_observed_empty_owner_proves_membership_absence(self):
        with self.assertRaises(ValueError):effects.native_variants({})
        self.assertEqual({},effects.native_variants(self.execution([])))

    def test_false_native_lookup_identity_is_preserved_not_filtered(self):
        old=self.execution([{'meta':1,'name':'duplicate','nameLookupIdentity':False}])
        self.assertFalse(effects.native_variants(old)[effects.OWNER+'#1']['variant']['nameLookupIdentity'])

    def test_duplicate_meta_or_wrong_owner_cannot_be_flattened_to_a_favorable_map(self):
        with self.assertRaisesRegex(ValueError,'duplicated'):
            effects.native_variants(self.execution([{'meta':1},{'meta':1}]))
        value=self.execution([]);value['customMetaItems']['items'][0]['registryName']='other:owner'
        with self.assertRaisesRegex(ValueError,'owner differs'):effects.native_variants(value)


if __name__=='__main__':unittest.main()
