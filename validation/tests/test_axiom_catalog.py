"""Full-catalog projection, native binding and qualification custody boundaries."""
from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools"))
import axiom_catalog_sources as source
import axiom_catalog_conformance as qualification
import axiom_native_material_sources as native


class CatalogTests(unittest.TestCase):
    def test_lock_covers_complete_producers_config_lifecycle_and_transformer(self):
        lock=json.loads(source.LOCK.read_bytes())
        self.assertEqual(26,len(lock["references"]))
        self.assertEqual({("gtceu",p) for p in source.GT_PATHS}|{("cleanroom",p) for p in source.CLEANROOM_PATHS}|{("supersymmetry",source.PACK_CONFIG)},
                         {(r["repository"],r["path"]) for r in lock["references"]})
        self.assertEqual(7,len(source.GROUPS))

    def test_unknown_missing_duplicate_and_changed_source_are_rejected(self):
        lock=json.loads(source.LOCK.read_bytes()); roots={r:Path("fixture") for r in lock["revisions"]}
        with patch.object(source,"git",return_value=b"changed"):
            with self.assertRaisesRegex(ValueError,"identity differs"): source.verify_sources(roots)
        for change in (lambda l:l["references"].clear(),lambda l:l["references"][0].update(path="../outside"),lambda l:l["revisions"].update(gtceu="0"*40)):
            altered=deepcopy(lock); change(altered)
            with self.assertRaises(ValueError): source.verify_sources(roots,altered)
        with self.assertRaises(ValueError): source.verify_sources({})

    def test_duplicate_and_missing_rows_reject_after_valid_prior_rows(self):
        lock=json.loads(source.LOCK.read_bytes()); roots={r:Path(r) for r in lock["revisions"]}
        raw={}
        for row in lock["references"]:
            value=(row["repository"]+":"+row["path"]).encode(); raw[row["repository"],row["path"]]=value
            row["sha256"]=sha256(value).hexdigest(); row["gitBlob"]=sha1(b"blob "+str(len(value)).encode()+b"\0"+value).hexdigest()
        with patch.object(source,"git",side_effect=lambda root,command,ref:raw[str(root),ref.split(":",1)[1]]):
            self.assertEqual(26,len(source.verify_sources(roots,lock)))
            duplicate=deepcopy(lock); duplicate["references"].append(duplicate["references"][0])
            with self.assertRaisesRegex(ValueError,"duplicate"): source.verify_sources(roots,duplicate)
            missing=deepcopy(lock); missing["references"].pop()
            with self.assertRaisesRegex(ValueError,"closure"): source.verify_sources(roots,missing)

    def test_original_default_annotation_and_category_are_preserved(self):
        text='@Config(modid = GTValues.MODID, name = GTValues.MODID + "/" + GTValues.MODID)\nclass ConfigHolder {\n'
        for holder,child,option in (("recipes","RecipeOptions","generateLowQualityGems"),("worldgen","WorldGenOptions","allUniqueStoneTypes")):
            text+='    @Config.Comment("original holder")\n    @Config.Name("'+child+'")\n    public static '+child+' '+holder+' = new '+child+'();\n'
            text+='    @Config.Comment("original option")\n    public boolean '+option+' = true;\n'
        text+='    @Config.Comment("compat holder")\n    @Config.Name("Compatibility Options")\n    public static CompatibilityOptions compat = new CompatibilityOptions();\n'
        text+='    @Config.Comment("priorities")\n    public String[] modPriorities = {"minecraft", "gregtech"};\n'
        projected=source.configuration_source(text+"}\n")
        self.assertIn('public boolean generateLowQualityGems = true;',projected)
        self.assertIn('public boolean allUniqueStoneTypes = true;',projected)
        self.assertIn('@Config.Name("RecipeOptions")',projected)
        self.assertIn('modid = "gregtech"',projected)
        self.assertIn('public String[] modPriorities = {"minecraft", "gregtech"};',projected)
        self.assertNotIn('boolean generateLowQualityGems = false',projected)

    def test_ambiguous_or_unannotated_config_rejects(self):
        for text in ('public boolean enabled = true;', '    @Config.Comment("c") public boolean enabled = true; public boolean enabled = false;'):
            with self.assertRaises(ValueError): source.annotated_field(text,"public boolean enabled =")
        with self.assertRaises(ValueError): source.configuration_source("class Missing {}")

    def test_catalog_binding_is_lazy_and_missing_does_not_mean_null(self):
        text=(native.HOST/"CatalogInputs.java").read_text()
        self.assertIn('field.get(null)',text)
        self.assertIn('material.catalog-field',text)
        self.assertNotIn('new FluidMaterial.Builder',text)
        self.assertNotIn('register()',text)
        self.assertIn('MaterialPhase.PRE',(native.HOST/"FluidEnvironment.java").read_text())

    def test_original_parser_and_native_event_bus_are_owners(self):
        files=native.assemble()
        self.assertIn('ConfigManager.class.getDeclaredMethod("sync"',files["CatalogConfiguration"])
        self.assertIn('new Configuration(',files["CatalogConfiguration"])
        self.assertNotIn('Boolean.parseBoolean',files["CatalogConfiguration"])
        self.assertNotIn('cfg.save(',files["CatalogConfiguration"])
        self.assertIn('MinecraftForge.EVENT_BUS.post',files["MaterialEvents"])
        self.assertIn('GenericEvent<FluidMaterial>',files["MaterialEvent"])
        self.assertNotIn('nativeevents',files["MaterialLifecycle"])

    def test_configuration_matrix_requires_real_effect_and_unchanged_materials(self):
        runs={(g,s):{"configuration":{"generateLowQualityGems":g,"allUniqueStoneTypes":s},
                     "snapshot":{"materials":{},"prefixes":str((g,s))}} for g in (False,True) for s in (False,True)}
        qualification.validate_config_matrix(runs)
        runs[True,False]["snapshot"]["materials"]={"fabricated":1}
        with self.assertRaisesRegex(ValueError,"material declarations"): qualification.validate_config_matrix(runs)
        runs[True,False]["snapshot"]["materials"]={}
        runs[True,False]["snapshot"]["prefixes"]=runs[False,False]["snapshot"]["prefixes"]
        with self.assertRaisesRegex(ValueError,"effect"): qualification.validate_config_matrix(runs)

    def test_no_generated_content_or_discovery_is_claimed(self):
        probe=qualification.FIXTURE.read_text()
        self.assertIn('hasFluidBlock',probe)
        self.assertIn('SourceMaterialCatalog.register()',probe)
        self.assertNotIn('registerMaterialFluids()',probe)
        self.assertNotIn('OreDictUnifier.init()',probe)
        self.assertIn('WorkerIsolation.install()',qualification.DRIVER.read_text())


if __name__=="__main__": unittest.main()
