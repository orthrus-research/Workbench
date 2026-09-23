"""Native item source custody, exact symbol bindings and qualification boundaries."""
from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools"))
import axiom_item_sources as source
import axiom_item_conformance as qualification
import axiom_native_material_sources as native


class NativeItemTests(unittest.TestCase):
    def test_complete_lock_and_retained_class_closure(self):
        lock=json.loads(source.LOCK.read_bytes())
        expected={("gtceu",p) for p in (*source.PATHS.values(),*source.AUDITED_GT)}|{("cleanroom",p) for p in source.CLEANROOM}|{("groovyscript",source.MAPPINGS)}
        self.assertEqual(expected,{(r["repository"],r["path"]) for r in lock["references"]})
        self.assertEqual(11,len(source.PATHS))
        self.assertTrue(set(source.PATHS).issubset(native.assemble()))

    def test_mutable_unknown_and_missing_sources_reject(self):
        lock=json.loads(source.LOCK.read_bytes()); roots={r:Path(r) for r in lock["revisions"]}
        with patch.object(source,"git",return_value=b"different"):
            with self.assertRaisesRegex(ValueError,"identity differs"): source.verify_sources(roots,lock)
        for mutation in (lambda l:l["references"].clear(),lambda l:l["revisions"].update(gtceu="HEAD"),lambda l:l["references"][0].update(path="../outside")):
            changed=deepcopy(lock); mutation(changed)
            with self.assertRaises(ValueError): source.verify_sources(roots,changed)
        with self.assertRaises(ValueError): source.verify_sources({})

    def test_duplicate_missing_and_changed_rows_after_valid_predecessors(self):
        lock=json.loads(source.LOCK.read_bytes()); roots={r:Path(r) for r in lock["revisions"]}; data={}
        for row in lock["references"]:
            raw=(row["repository"]+":"+row["path"]).encode(); data[row["repository"],row["path"]]=raw
            row.update(sha256=sha256(raw).hexdigest(),gitBlob=sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest())
        with patch.object(source,"git",side_effect=lambda root,command,ref:data[str(root),ref.split(":",1)[1]]),patch.object(source,"validate_bindings"):
            self.assertEqual(len(lock["references"]),len(source.verify_sources(roots,lock)))
            duplicate=deepcopy(lock); duplicate["references"].append(duplicate["references"][0])
            with self.assertRaisesRegex(ValueError,"duplicate"): source.verify_sources(roots,duplicate)
            missing=deepcopy(lock); missing["references"].pop()
            with self.assertRaisesRegex(ValueError,"closure"): source.verify_sources(roots,missing)

    def test_only_stack_calls_are_rebound(self):
        text='package gregtech.api.unification; class OreDictUnifier { void f() { itemStack.isEmpty(); modPriorities.isEmpty(); delegate.isEmpty(); event.getOre().copy(); material.copy(); itemStacks.get(0).copy(); } }'
        result=source.extract("OreDictUnifier",text)
        self.assertIn('itemStack.func_190926_b()',result)
        for retained in ('modPriorities.isEmpty()','delegate.isEmpty()','material.copy()'): self.assertIn(retained,result)
        self.assertIn('event.getOre().func_77946_l()',result)
        self.assertIn('itemStacks.get(0).func_77946_l()',result)
        with self.assertRaises(ValueError): source.extract("Unknown",text)

    def test_native_damage_is_not_metadata(self):
        self.assertEqual('func_77952_i',source.METHODS['getItemDamage'])
        self.assertIn('func_77952_i()',(native.HOST/'ItemAndMetadata.java').read_text())
        self.assertIn('getDamage(ItemStack stack)',qualification.FIXTURE.read_text())
        self.assertIn('getMetadata(ItemStack stack)',qualification.FIXTURE.read_text())
        with self.assertRaisesRegex(ValueError,"symbol differs"): source.validate_bindings('FD: a/b a/b')

    def test_source_symbols_are_owner_qualified(self):
        fields,methods=source.native_symbols('FD: a/field_1 a/name\nMD: a/func_1 ()I a/value ()I\nMD: b/func_2 ()I b/value ()I\n')
        self.assertEqual('field_1',fields['a','name'])
        self.assertEqual({'func_1'},methods['a','value']); self.assertEqual({'func_2'},methods['b','value'])

    def test_unifier_is_full_body_and_original_owner_bindings(self):
        text=(native.HOST/'OreDictUnifier.java').read_text()
        for name in ('getDust','getIngot','getGem','getUnificated','getAllWithOreDictionaryName','onItemRegistration'):
            self.assertIn(name+'(',text)
        self.assertIn('MinecraftForge.EVENT_BUS.register(OreDictUnifier.class)',text)
        self.assertIn('OreDictionary.getOreNames()',text)
        self.assertIn('FluidEnvironment.current().modPriorities()',text)
        self.assertNotIn('new Item()',text)

    def test_bootstrap_stops_after_native_item_not_full_recipe_bootstrap(self):
        text=(qualification.ORACLES/'NativeIdentityInputs.java').read_text()
        self.assertIn('net/minecraft/item/Item',text)
        self.assertIn('func_150900_l',text)
        self.assertIn('prefixDriftRejections", 11',text)
        probe=qualification.FIXTURE.read_text()
        self.assertNotIn('RECIPES.clear',probe)
        self.assertNotIn('func_193377_a()',probe)
        self.assertIn('six-explicit-native-fixtures',probe)
        self.assertIn('retry deduplicates before event',probe)

    def test_order_contract_is_exact_not_set_membership(self):
        value={'result':{'unifier':{'selectedIron':['a','b']}}}
        qualification.assert_order(value,['a','b'])
        with self.assertRaises(ValueError): qualification.assert_order(value,['b','a'])
        self.assertIn('S:modPriorities <',qualification.priority_config([]))
        self.assertEqual(2,qualification.priority_config(['duplicate','duplicate']).count('duplicate'))

    def test_source_overrides_are_qualification_only(self):
        loader=(native.SHARED/'NativeVanillaIdentities.java').read_text()
        self.assertIn('program class boundary differs',loader)
        self.assertIn('WorkerIsolation.install()',qualification.SOURCE_DRIVER.read_text())
        self.assertNotIn('NativeItemSourceConformance', (ROOT/'modules/axiom/jvm/build.gradle.kts').read_text())
        self.assertIn('cleanroomSourceExecutionIdentical',Path(qualification.__file__).read_text())


if __name__=='__main__': unittest.main()
