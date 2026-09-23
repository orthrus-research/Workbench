"""Material block source custody, native owner bindings and composition boundaries."""
from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_material_block_sources as source
import axiom_material_block_conformance as qualification
import axiom_native_material_sources as native


class MaterialBlockTests(unittest.TestCase):
    def test_complete_source_inventory_and_native_only_distribution(self):
        lock = json.loads(source.LOCK.read_bytes())
        expected = {("gtceu", p) for p in (*source.PATHS.values(), *source.AUDITED_GT)} | {("cleanroom", p) for p in source.CLEANROOM} | {("groovyscript", source.items.MAPPINGS)}
        self.assertEqual(expected, {(r["repository"], r["path"]) for r in lock["references"]})
        self.assertTrue(set(source.NAMES).issubset(native.assemble()))
        self.assertFalse((native.SHARED / "BlockCompressed.java").exists())

    def test_identity_revision_and_path_rejection(self):
        lock = json.loads(source.LOCK.read_bytes()); roots = {r: Path(r) for r in lock["revisions"]}
        with patch.object(source, "git", return_value=b"changed"):
            with self.assertRaisesRegex(ValueError, "identity differs"): source.verify_sources(roots, lock)
        for mutate in (lambda l: l["references"].clear(), lambda l: l["revisions"].update(gtceu="HEAD"),
                       lambda l: l["references"][0].update(path="../outside")):
            changed = deepcopy(lock); mutate(changed)
            with self.assertRaises(ValueError): source.verify_sources(roots, changed)
        with self.assertRaises(ValueError): source.verify_sources({})

    def test_missing_or_duplicate_source_rows_reject(self):
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

    def test_sparse_groups_and_source_sentinel_not_replaced(self):
        text = (native.HOST / "MaterialBlockDeclarations.java").read_text()
        for part in ("id / 16", "id % 16", "new FluidMaterial[16]", 'PrefixDependencies.material("NULL")',
                     "new Int2ObjectAVLTreeMap<>()", "COMPRESSED.put(m, block)", "FRAMES.put(m, block)",
                     "!OrePrefix.block.isIgnored(material)", "MaterialFlags.GENERATE_FRAME"):
            self.assertIn(part, text)
        self.assertNotIn("GTRecipeManager", text)
        self.assertNotIn("registerTileEntity", text)
        self.assertNotIn("createSurfaceRockBlock", text)

    def test_owner_qualified_native_method_and_map_bindings(self):
        base = (native.HOST / "BlockMaterialBase.java").read_text()
        form = (native.HOST / "MaterialItemBlock.java").read_text()
        declarations = (native.HOST / "MaterialBlockDeclarations.java").read_text()
        for part in ("func_180661_e()", "func_176203_a(int meta)", "func_176201_c(", "stack.func_77960_j()"):
            self.assertIn(part, base)
        self.assertIn("int func_77647_b(int damage)", form)
        self.assertIn("BlockMaterialBase func_179223_d()", form)
        self.assertIn("state.func_177230_c()", declarations)
        self.assertIn("entry.getValue()", declarations)
        self.assertNotIn("entry.func_177229_b()", declarations)
        with self.assertRaisesRegex(ValueError, "binding differs"): source.validate_bindings("FD: a/b a/b")

    def test_unqualified_native_overrides_have_compiler_guards(self):
        for filename, method in (("BlockFrame", "func_180639_a"), ("BlockCompressed", "func_190948_a"),
                                 ("BlockMaterialBase", "getFlammability"), ("MaterialItemBlock", "func_77653_i")):
            text = (native.HOST / (filename + ".java")).read_text()
            self.assertRegex(text, r"@Override\s+public [^{]+" + method + r"\([^}]+throw new Failure")
        frame = (native.HOST / "BlockFrame.java").read_text()
        self.assertNotIn("setFrameMaterial", frame)
        self.assertNotIn("ModelLoader", frame)

    def test_composition_is_fixed_before_native_registration(self):
        text = (native.HOST / "MaterialContentFamily.java").read_text()
        self.assertFalse((native.HOST / "MaterialItemFamily.java").exists())
        for part in ("private final Universe universe", "Phase.BLOCK_REGISTERING", "Phase.BLOCKS_REGISTERED", "Phase.FAILED",
                     "MaterialBlockDeclarations.COMPRESSED.equals(compressedIndex)", "MetaItem.getMetaItems().equals(items)",
                     "Item.func_150898_a(b) != blockItems.get(b)", "RegistryEvent.Register<Block>"):
            self.assertIn(part, text)
        self.assertLess(text.index("OreDictUnifier.init()"), text.index("MaterialBlockDeclarations.construct()"))
        self.assertLess(text.index("MaterialBlockDeclarations.construct()"), text.index("MaterialItemDeclarations.construct()"))
        self.assertLess(text.index("MaterialItemDeclarations.register(event.getRegistry())"), text.index("MaterialBlockDeclarations.registerItems(event.getRegistry())"))
        self.assertNotIn("runMaterialHandlers()", text)

    def test_expected_frame_edit_keeps_other_content_unchanged(self):
        blocks = [{"prefix": "frameGt", "registryName": "g:frame", "variants": [{"material": "g:a"}, {"material": "gregtech:null"}]},
                  {"prefix": "block", "registryName": "g:block", "variants": [{"material": "g:a"}]}]
        value = {"inventory": {"blocks": blocks}, "checkpoints": [{"blocks": deepcopy(blocks)}],
                 "events": [["frameGtA", ["g:frame"]], ["blockA", ["g:block"]]],
                 "checks": {"generatedBlockVariants": 2, "nullSlots": 1}}
        changed = qualification.without_frames(value)
        self.assertEqual(1, changed["checks"]["generatedBlockVariants"])
        self.assertEqual(0, changed["checks"]["nullSlots"])
        self.assertEqual([blocks[1]], changed["inventory"]["blocks"])
        self.assertEqual([["blockA", ["g:block"]]], changed["events"])
        self.assertEqual(2, len(value["inventory"]["blocks"]))

    def test_catalog_bridge_admits_source_declared_material_subtypes(self):
        text = (native.HOST / "CatalogInputs.java").read_text()
        self.assertIn("FluidMaterial.class.isAssignableFrom(field.getType())", text)
        self.assertNotIn("field.getType() == FluidMaterial.class", text)
        self.assertIn("Modifier.isPublic(field.getModifiers())", text)
        self.assertIn("Modifier.isStatic(field.getModifiers())", text)

    def test_mutation_difference_diagnostics_are_bounded(self):
        self.assertEqual("result.a[0]: x != y", qualification.first_difference({"a": ["x"]}, {"a": ["y"]}))
        self.assertEqual("result: different keys", qualification.first_difference({"a": 1}, {"b": 1}))


if __name__ == "__main__": unittest.main()
