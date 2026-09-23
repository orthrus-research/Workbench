"""Ore source custody, native-only projection, transformation and checkpoint boundaries."""
from copy import deepcopy
from hashlib import sha1, sha256
import io
import json
from pathlib import Path
import sys
import unittest
import tempfile
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_material_ore_sources as source
import axiom_native_material_sources as native
import build_axiom


class MaterialOreTests(unittest.TestCase):
    def test_complete_source_inventory_and_native_only_distribution(self):
        lock = json.loads(source.LOCK.read_bytes())
        self.assertEqual(source.EXPECTED, {(r["repository"], r["path"]) for r in lock["references"]})
        self.assertEqual(source.revisions(), lock["revisions"])
        self.assertTrue(set(source.NAMES).issubset(native.assemble()))
        self.assertFalse((native.SHARED / "BlockOre.java").exists())

    def test_source_custody_rejects_drift_and_duplicate_rows(self):
        roots = {r: Path(r) for r in source.revisions()}
        lock = json.loads(source.LOCK.read_bytes())
        changed = deepcopy(lock); changed["revisions"]["susy-core"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "revisions"): source.verify_sources(roots, changed)
        with patch.object(source, "git", return_value=b"different"):
            with self.assertRaisesRegex(ValueError, "identity differs"): source.verify_sources(roots, lock)
        fake = deepcopy(lock)
        for row in fake["references"]:
            row.update(sha256=sha256(b"x").hexdigest(), gitBlob=sha1(b"blob 1\0x").hexdigest())
        with patch.object(source, "git", return_value=b"x"), patch.object(source.blocks, "validate_bindings"):
            self.assertEqual(source.EXPECTED, set(source.verify_sources(roots, fake)))
            fake["references"].append(fake["references"][0])
            with self.assertRaisesRegex(ValueError, "duplicate"): source.verify_sources(roots, fake)

    def test_nonordinary_source_paths_and_incomplete_closure_reject(self):
        roots = {r: Path(r) for r in source.revisions()}
        lock = json.loads(source.LOCK.read_bytes()); lock["references"][0]["path"] = "../private"
        with self.assertRaises(ValueError): source.verify_sources(roots, lock)
        lock["references"] = []
        with self.assertRaisesRegex(ValueError, "closure"): source.verify_sources(roots, lock)

    def test_generation_preserves_first_null_truncation(self):
        text = (native.HOST / "OreDeclarations.java").read_text()
        for fragment in ("ArrayUtils.indexOf(src, null)", "Arrays.copyOfRange(src, 0, nullIndex", "id / 16", "stoneTypeBuffer[id % 16]", "MaterialFlags.DISABLE_ORE_BLOCK"):
            self.assertIn(fragment, text)
        self.assertNotIn("filter(Objects::nonNull)", text)

    def test_original_drop_paths_stay_distinct(self):
        text = (native.HOST / "BlockOre.java").read_text()
        self.assertIn("stoneType.shouldBeDroppedAsItem || StoneType.STONE_TYPE_REGISTRY.getIDForObject(stoneType) < 16", text)
        self.assertIn("OreDeclarations.getOreForMaterial(this.material).get(StoneTypes.STONE)", text)
        self.assertIn("return super.func_180643_i(this.func_176223_P());", text)
        self.assertIn("this.field_176227_L = stateContainer;", text)
        self.assertNotIn("setAccessible", text)

    def test_unqualified_native_overrides_are_explicit(self):
        for filename, method in (("BlockOre", "isFireSource"), ("OreItemBlock", "func_77653_i"), ("VariantBlock", "func_190948_a"), ("StoneVariantBlock", "func_180660_a")):
            text = (native.HOST / (filename + ".java")).read_text()
            self.assertRegex(text, r"@Override\s+(?:@SideOnly\(Side.CLIENT\)\s+)?public [^{]+" + method + r"\([^}]+throw new Failure")
        self.assertIn('throw new Failure("incomplete", "ore.jei"', (native.HOST / "StoneType.java").read_text())

    def test_selected_susy_assignments_are_not_full_producer_claims(self):
        self.assertEqual(19, sum(map(len, source.HOST_MATERIALS.values())))
        self.assertIn("Forsterite", source.HOST_MATERIALS["SuSyFirstDegreeMaterials"])
        self.assertNotIn("KreepBasalt", source.HOST_MATERIALS["SuSySecondDegreeMaterials"])
        self.assertTrue(source.EXTERNAL_NAMES.isdisjoint(native.assemble()))
        for name in source.EXTERNAL_NAMES: self.assertFalse((native.HOST / (name + ".java")).exists())

    def test_access_rules_and_original_transformer_are_distributed_as_source(self):
        text = (native.HOST / "OreAccessRules.java").read_text()
        self.assertIn("public-f net.minecraft.block.Block field_176227_L", text)
        self.assertIn("public net.minecraft.block.Block field_149782_v", text)
        driver = (native.SHARED / "NativeMaterialAccess.java").read_text()
        self.assertIn("processATFile", driver)
        self.assertIn("AccessTransformer", driver)
        self.assertNotIn("ClassWriter", driver)
        self.assertNotIn("ACC_FINAL", driver)
        self.assertIn('from("../sources/material-ores.lock.json")', (ROOT / "modules/axiom/jvm/build.gradle.kts").read_text())

    def test_composed_source_generation_precedes_registration(self):
        text = (native.HOST / "MaterialContentFamily.java").read_text()
        self.assertLess(text.index("oreContent.generate()"), text.index("MaterialBlockDeclarations.registerBlocks(event.getRegistry())"))
        self.assertIn("Phase.ORE_HOSTS_PREPARED", text)
        self.assertIn("phase != Phase.COMPLETE || !includesOres()", text)
        ore = (native.HOST / "OreContentFamily.java").read_text()
        addon = (ROOT / "modules/axiom/tests/oracles/NativeOreAddonProbe.java").read_text()
        self.assertLess(addon.index("SusyStoneTypes.init()"), addon.index("new SusyStoneVariantBlock("))
        self.assertIn("finally { loader.setActiveModContainer(previous); }", ore)
        self.assertIn("!OreDeclarations.oreBlockTable.equals(index)", ore)

    def test_distribution_rejects_separately_supplied_addon_sources_and_classes(self):
        notices = ("LICENSE", "NOTICE.md", "UPSTREAM-NOTICE.md", "third-party/fastutil/LICENSE",
                   "third-party/groovy-and-tomlj/LICENSE", "third-party/groovy-and-tomlj/NOTICE",
                   "third-party/checker-qual/LICENSE.txt", "third-party/forge/LGPL-2.1.txt", "third-party/asm.txt",
                   "third-party/guava-failureaccess-jspecify/LICENSE", "third-party/log4j-api/LICENSE",
                   "third-party/log4j-api/NOTICE", "third-party/log4j-core/LICENSE", "third-party/log4j-core/NOTICE",
                   "third-party/commons-lang3/LICENSE.txt", "third-party/commons-lang3/NOTICE.txt")
        with tempfile.TemporaryDirectory() as temp:
            for i, member in enumerate(("native/OreAddon.java", "native/SusyStoneMaterials.java", "native/SusyStoneTypes.class", "native/SusyStoneVariantBlock$StoneType.class")):
                nested = io.BytesIO()
                with zipfile.ZipFile(nested, "w") as jar: jar.writestr(member, b"fixture")
                archive = Path(temp) / ("engine" + str(i) + ".zip"); prefix = archive.stem + "/"
                with zipfile.ZipFile(archive, "w") as out:
                    for name in notices: out.writestr(prefix + name, b"fixture")
                    out.writestr(prefix + "lib/engine.jar", nested.getvalue())
                if i == 0: build_axiom.verify_archive(archive)
                else:
                    with self.assertRaisesRegex(ValueError, "separately supplied addon"): build_axiom.verify_archive(archive)


if __name__ == "__main__": unittest.main()
