"""Material-prefix source custody, family boundaries and native overload admission."""
from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_material_item_sources as source
import axiom_material_item_conformance as qualification
import axiom_native_material_sources as native


class MaterialItemTests(unittest.TestCase):
    def test_source_inventory_and_native_only_distribution(self):
        lock = json.loads(source.LOCK.read_bytes())
        expected = {("gtceu", p) for p in (*source.PATHS.values(), *source.AUDITED_GT)} | {("cleanroom", p) for p in source.CLEANROOM} | {("groovyscript", source.items.MAPPINGS)}
        self.assertEqual(expected, {(r["repository"], r["path"]) for r in lock["references"]})
        self.assertTrue(set(source.NAMES).issubset(native.assemble()))
        self.assertFalse((native.SHARED / "MetaItem.java").exists())

    def test_source_identity_revision_and_root_rejection(self):
        lock = json.loads(source.LOCK.read_bytes()); roots = {r: Path(r) for r in lock["revisions"]}
        with patch.object(source, "git", return_value=b"changed"):
            with self.assertRaisesRegex(ValueError, "identity differs"): source.verify_sources(roots, lock)
        for mutate in (lambda l: l["references"].clear(), lambda l: l["revisions"].update(gtceu="HEAD"),
                       lambda l: l["references"][0].update(path="../outside")):
            changed = deepcopy(lock); mutate(changed)
            with self.assertRaises(ValueError): source.verify_sources(roots, changed)
        with self.assertRaises(ValueError): source.verify_sources({})

    def test_valid_predecessors_do_not_admit_missing_or_duplicate_rows(self):
        lock = json.loads(source.LOCK.read_bytes()); data = {}; roots = {r: Path(r) for r in lock["revisions"]}
        for row in lock["references"]:
            raw = (row["repository"] + ":" + row["path"]).encode(); data[row["repository"], row["path"]] = raw
            row.update(sha256=sha256(raw).hexdigest(), gitBlob=sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest())
        with patch.object(source, "git", side_effect=lambda root, command, ref: data[str(root), ref.split(":", 1)[1]]), patch.object(source, "validate_bindings"):
            self.assertEqual(len(data), len(source.verify_sources(roots, lock)))
            duplicate = deepcopy(lock); duplicate["references"].append(duplicate["references"][0])
            with self.assertRaisesRegex(ValueError, "duplicate"): source.verify_sources(roots, duplicate)
            missing = deepcopy(lock); missing["references"].pop()
            with self.assertRaisesRegex(ValueError, "closure"): source.verify_sources(roots, missing)

    def test_member_extraction_preserves_nested_blocks_and_literal_braces(self):
        text = 'prefix\npublic void method() { String s="}"; /* } */ if (true) { f(); } }\nsuffix'
        self.assertEqual(text.split('\n')[1], source.block(text, "public void method()"))
        for value in (text + text, "public void method() {", "different"):
            with self.assertRaises(ValueError): source.block(value, "public void method()")
        with self.assertRaises(ValueError): source.field("", "missing")

    def test_native_damage_metadata_and_forge_overloads_are_distinct(self):
        self.assertEqual(("ItemStack", "func_77952_i"), source.METHOD_BINDINGS["getItemDamage"])
        self.assertEqual(("ItemStack", "func_77960_j"), source.METHOD_BINDINGS["getMetadata"])
        text = (native.HOST / "MetaPrefixItem.java").read_text()
        self.assertIn("public int getItemStackLimit(", text)
        self.assertNotIn("func_77639_j(", text)
        meta = (native.HOST / "MetaItem.java").read_text()
        for method in ("hasContainerItem", "getContainerItem", "getItemEnchantability"):
            self.assertIn(method + "(", meta)
        with self.assertRaisesRegex(ValueError, "binding differs"): source.validate_bindings("FD: a/b a/b")

    def test_original_order_metadata_and_whole_capability_method(self):
        prefix = (native.HOST / "MetaPrefixItem.java").read_text(); meta = (native.HOST / "MetaItem.java").read_text()
        for text in ("registry.getIDForObject(material)", "metaItems.keySet()", "registerSpecialOreDict(item, material, prefix)",
                     "orePrefix.doGenerateItem(material)", 'PrefixDependencies.material("Uranium238")'):
            self.assertIn(text, prefix)
        for text in ("new Short2ObjectLinkedOpenHashMap<>()", "providers.add(provider.createProvider(stack))", "new CombinedCapabilityProvider(providers)",
                     "Short.MAX_VALUE - 1", "names.put(unlocalizedName, metaValueItem)"):
            self.assertIn(text, meta)

    def test_checkpoint_is_family_scoped_and_failures_are_terminal(self):
        text = (native.HOST / "MaterialContentFamily.java").read_text()
        for fragment in ("Phase.FAILED", "phase != Phase.COMPLETE", "RegistryEvent.Register<Item>", "finally { MinecraftForge.EVENT_BUS.unregister(listener); }"):
            self.assertIn(fragment, text)
        for forbidden in ("RECIPES.clear", "GTRecipeManager", "runMaterialHandlers()", "MetaBlocks.init()", "ToolItems.init()"):
            self.assertNotIn(forbidden, text)
        self.assertIn('"allItemBehaviorQualified", false', text)
        self.assertIn("MetaItem.getMetaItems().equals(items)", text)
        self.assertLess(text.index("OreDictUnifier.init()"), text.index("MaterialItemDeclarations.construct()"))
        for filename in ("MetaItem", "MetaPrefixItem"):
            self.assertIn('new Failure("incomplete", "item.behavior"', (native.HOST / (filename + ".java")).read_text())

    def test_family_declarations_preserve_original_list_and_separate_callbacks(self):
        text = (native.HOST / "MaterialItemDeclarations.java").read_text()
        self.assertEqual(39, text.count("orePrefixes.add("))
        self.assertLess(text.index("registry.register(item)"), text.index("item.registerSubItems()"))
        for absent in ("new MetaItem1", "new MetaArmor", "GLASS_LENSES", "GTRecipeManager", "new Item()"):
            self.assertNotIn(absent, text)

    def test_expected_source_edit_removes_only_plate_not_other_families(self):
        items = [{"prefix": "plate", "variants": [1, 2]}, {"prefix": "plateDouble", "variants": [3]}]
        value = {"inventory": {"items": items}, "checkpoints": [{"items": deepcopy(items)}],
                 "events": [["plateA", ["gregtech:meta_plate"]], ["plateDoubleA", ["gregtech:meta_plate_double"]]],
                 "checks": {"generatedStackVectors": 3}}
        result = qualification.without_plate(value)
        self.assertEqual(1, result["checks"]["generatedStackVectors"])
        self.assertEqual([3], result["inventory"]["items"][1]["variants"])
        self.assertEqual(1, len(result["events"]))
        self.assertEqual([1, 2], value["inventory"]["items"][0]["variants"])


if __name__ == "__main__": unittest.main()
