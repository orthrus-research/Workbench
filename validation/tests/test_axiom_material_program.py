"""Source/corpus custody only; these checks do not qualify Groovy execution."""
from copy import deepcopy
import json
import importlib.util
from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_material_program_sources as source
import axiom_material_program_cases as corpus


class MaterialProgramSourceTests(unittest.TestCase):
    def test_smoke_retains_complete_exact_programs_outside_native_output(self):
        from axiom_material_runtime_smoke import retain_programs
        files = {'groovy/runConfig.json': b'{}\r\n', 'groovy/preInit/Probe.groovy': b'// original\r\n',
                 'config/\U0001f600.bin': b'\x00\xff'}
        with TemporaryDirectory() as temporary:
            report = Path(temporary) / 'report.json'
            rows = retain_programs(report, [{'name': name, 'files': files} for name in ('before', 'after')])
            self.assertEqual(rows, json.loads((Path(temporary) / 'report.json.inputs/programs.json').read_bytes()))
            archives = []
            for row in rows.values():
                self.assertEqual(corpus.identity(files), row['sourceProgram'])
                pin = row['programArchive']; path = report.parent / pin['path']; raw = path.read_bytes()
                self.assertEqual(len(raw), pin['size']); self.assertEqual(sha256(raw).hexdigest(), pin['sha256'])
                with ZipFile(path) as archive:
                    self.assertEqual(files, {name: archive.read(name) for name in archive.namelist()})
                archives.append(raw)
            self.assertEqual(archives[0], archives[1])
            with self.assertRaisesRegex(ValueError, 'must be new'):
                retain_programs(report, [])
            self.assertEqual(archives[0], (report.parent / rows['before']['programArchive']['path']).read_bytes())

    def test_native_source_acknowledgement_binds_all_unicode_paths_and_configuration_bytes(self):
        files={"groovy/\U00010000.groovy":b"// CRLF\r\n", "groovy/\ue000.groovy":b"// BMP\n",
               "config/caf\u00e9.cfg":b"\x00\xff"}
        expected=corpus.source_acknowledgement(files)
        self.assertEqual(3,expected["fileCount"])
        self.assertTrue(corpus.matches_source_acknowledgement(expected,files))
        for changes in ({"fileCount":True},{"sha256":"0"*64},{"schema":"unknown"},{"files":[]},
                        {"scope":"groovy-only"},{"inventoryEncoding":"ascii"}):
            self.assertFalse(corpus.matches_source_acknowledgement({**expected,**changes},files))
        self.assertFalse(corpus.matches_source_acknowledgement(expected,{**files,"config/new.cfg":b""}))
        self.assertFalse(corpus.matches_source_acknowledgement(expected,{p:b for p,b in files.items() if p.startswith("groovy/")}))

    def pack_boundary_fixture(self):
        import axiom_material_pack_boundary as boundary
        files = {path: ('// whole file\r\npackage ' + path.split('/')[1] + '\n// final line\n').encode()
                 for path in boundary.PACK_FILES.values()}
        lock = {'schema': 'axiom.material-program-source-lock.v1', 'revisions': source.selected_revisions(),
                'references': [{'repository': 'supersymmetry', 'path': path, **source.source_identity(raw)}
                               for path, raw in files.items()]}
        return boundary, files, lock

    def test_whole_pack_boundaries_keep_every_original_byte_and_complete_base(self):
        boundary, files, lock = self.pack_boundary_fixture()
        calls = []
        def git(root, operation, ref):
            calls.append((root, operation, ref))
            return files[ref.split(':', 1)[1]]
        with patch.object(source, 'git', side_effect=git):
            selected = boundary.pack_programs(Path('/selected-pack'), lock=lock)
        base = corpus.cases()[0]['files']
        self.assertEqual(2, len(selected))
        for case in selected:
            self.assertEqual(len(base) + 1, len(case['files']))
            self.assertEqual(base, {path: case['files'][path] for path in base})
            self.assertEqual(files[case['source']['path']], case['files'][case['source']['path']])
            self.assertEqual(corpus.identity(case['files']), case['program'])
        self.assertTrue(all(operation == 'show' and ref.startswith(lock['revisions']['supersymmetry'] + ':')
                            for _, operation, ref in calls))

    def test_whole_pack_boundary_refuses_source_or_selection_drift(self):
        boundary, files, lock = self.pack_boundary_fixture()
        with patch.object(source, 'git', return_value=b'edited working-tree substitute'):
            with self.assertRaisesRegex(ValueError, 'differs from selected source'):
                boundary.pack_programs(Path('/selected-pack'), lock=lock)
        changed = deepcopy(lock); changed['revisions']['supersymmetry'] = '0' * 40
        with patch.object(source, 'git') as git:
            with self.assertRaisesRegex(ValueError, 'authority differs'):
                boundary.pack_programs(Path('/selected-pack'), lock=changed)
            git.assert_not_called()
        for references in ([], [lock['references'][0], *lock['references']]):
            with self.assertRaisesRegex(ValueError, 'one locked whole file'):
                boundary.pack_programs(Path('/selected-pack'), lock={**lock, 'references': references})

    def test_pack_boundary_requires_incomplete_native_composition_and_whole_source_evidence(self):
        boundary, files, lock = self.pack_boundary_fixture()
        with patch.object(source, 'git', side_effect=lambda root, operation, ref: files[ref.split(':', 1)[1]]):
            case = boundary.pack_programs(Path('/selected-pack'), lock=lock)[0]
        response = {'status': 'incomplete', 'result': {
            'nativeOutcome': 'incomplete', 'groovyExecutionQualified': False, 'wholePackParity': False,
            'sourceProgram': corpus.source_acknowledgement(case['files']), 'sourceAdmission': {'structurallyAdmitted': True},
            'execution': {'nativeCompilationFailure': True, 'cleanObservation': False, 'executionCompleted': True,
                'registeredMaterials': 605, 'phase': 'FROZEN', 'contentProgress': {'phase': 'COMPLETE'},
                'diagnostics': [{'compilerFindings': [{'location': {'path': case['source']['path']}}]}]}}}
        self.assertEqual([], boundary.check_result(case, response, 4))
        for field, value in [('status', 'accepted'), ('status', 'source-error')]:
            bad = deepcopy(response); bad[field] = value
            self.assertTrue(boundary.check_result(case, bad, 4))
        for field, value in [('sourceProgram', case['baseProgram']), ('wholePackParity', True)]:
            bad = deepcopy(response); bad['result'][field] = value
            self.assertTrue(boundary.check_result(case, bad, 4))
        for field, value in [('nativeCompilationFailure', False), ('cleanObservation', True), ('diagnostics', []),
                             ('executionCompleted', False), ('registeredMaterials', 602), ('contentProgress', {'phase': 'NOT_STARTED'})]:
            bad = deepcopy(response); bad['result']['execution'][field] = value
            self.assertTrue(boundary.check_result(case, bad, 4))
        self.assertTrue(boundary.check_result(case, response, 0))

    def profile_extension(self):
        root=ROOT / "profiles/packs/supersymmetry/src/workbench_profile_supersymmetry"
        spec=importlib.util.spec_from_file_location("axiom_profile_policy_test",root / "axiom.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return root,module

    def test_profile_admission_selection_binds_both_policies_without_claiming_availability(self):
        root,module=self.profile_extension()
        with patch.object(module,"files",return_value=root):
            selected=module.material_admission("supersymmetry:material-authoring-gt-base")
            with self.assertRaisesRegex(ValueError,"Unknown"):
                module.material_admission("../../candidate-policy")
        self.assertEqual(sha256((root / "axiom-material-admission.json").read_bytes()).hexdigest(),selected["sha256"])
        self.assertEqual(sha256((root / "axiom-material-contexts.json").read_bytes()).hexdigest(),selected["contextPolicySha256"])
        self.assertEqual("pending-native-admission",selected["policy"]["qualification"])
        self.assertNotIn("installedOperationAvailable",selected)

    def test_current_pack_preparation_covers_selected_native_context_and_refuses_missing_artifact(self):
        root,module=self.profile_extension()
        context="supersymmetry:material-authoring-pack"
        with patch.object(module,"files",return_value=root):
            prepared=module.preparation_inputs(context)
            assembly=module.assembly_policy(context)
        self.assertEqual(len(assembly["nativeContext"]["artifactDescriptors"]),len(prepared["artifacts"]))
        self.assertEqual({row["path"] for row in prepared["artifacts"]},
                         {row["path"] for row in assembly["artifactInputs"]})
        with TemporaryDirectory() as temporary:
            changed=Path(temporary)
            for name in ("axiom-material-contexts.json","axiom-native-inputs.json","axiom-native-early-context.json"):
                (changed/name).write_bytes((root/name).read_bytes())
            inputs=json.loads((changed/"axiom-native-inputs.json").read_bytes())
            inputs["artifacts"].pop()
            (changed/"axiom-native-inputs.json").write_text(json.dumps(inputs))
            with patch.object(module,"files",return_value=changed):
                with self.assertRaisesRegex(ValueError,"preparation differs"):
                    module.preparation_inputs(context)

    def test_profile_admission_cannot_belong_to_another_context_or_owner(self):
        root,module=self.profile_extension()
        context=(root / "axiom-material-contexts.json").read_bytes()
        for key in ("context","profile","schema"):
            changed=json.loads((root / "axiom-material-admission.json").read_bytes());changed[key]="wrong"
            with patch.object(module,"files") as resources:
                resources.return_value.joinpath.return_value.read_bytes.side_effect=[context,json.dumps(changed).encode()]
                with self.assertRaisesRegex(ValueError,"owner differs"):
                    module.material_admission("supersymmetry:material-authoring-gt-base")

    def test_pack_context_has_distinct_identity_and_cannot_claim_completed_composition(self):
        root,module=self.profile_extension()
        context="supersymmetry:material-authoring-pack"
        with patch.object(module,"files",return_value=root):
            pack=module.material_admission(context)
            base=module.material_admission("supersymmetry:material-authoring-gt-base")
            catalog=module.material_contexts()["policy"]
        self.assertNotEqual(base["sha256"],pack["sha256"])
        self.assertEqual(context,pack["policy"]["context"])
        self.assertEqual({**base["policy"],"nativePrivateMethods":{},"nativeMetaClassMethods":{},"nativeWriteFields":{}},{**pack["policy"],"nativePrivateMethods":{},"nativeMetaClassMethods":{},"nativeWriteFields":{},"context":base["context"],"nativeExtensions":base["policy"]["nativeExtensions"],
                                        "nativeReadFields":{},"nativeObjectMappers":[],"listOperations":base["policy"]["listOperations"],
                                        "referenceCasts":base["policy"]["referenceCasts"],
                                        "mapTypes":base["policy"]["mapTypes"],
                                        "numericOperations":base["policy"]["numericOperations"],
                                        "staticFields":base["policy"]["staticFields"],
                                        "constructors":base["policy"]["constructors"],
                                        "nativeMethodMappings":base["policy"]["nativeMethodMappings"],
                                        "compilerStaticCalls":base["policy"]["compilerStaticCalls"],
                                        "compilerStaticFields":base["policy"]["compilerStaticFields"],
                                        "nativeMethods":base["policy"]["nativeMethods"]})
        self.assertEqual(["java/lang/Byte#valueOf(B)Ljava/lang/Byte;"],
                         [item for item in pack["policy"]["compilerStaticCalls"] if item not in base["policy"]["compilerStaticCalls"]])
        self.assertEqual(["java/lang/Byte#TYPE:Ljava/lang/Class;", "java/lang/Float#TYPE:Ljava/lang/Class;"],
                         [item for item in pack["policy"]["compilerStaticFields"] if item not in base["policy"]["compilerStaticFields"]])
        self.assertEqual(["toLowerCaseUnderscore(Ljava/lang/String;)Ljava/lang/String;"],
                         pack["policy"]["nativeMethods"]["gregtech.api.util.GTUtility"])
        self.assertEqual(["getAt(I)Lnet/minecraft/item/ItemStack;", "getAt(Lgroovy/lang/IntRange;)Ljava/lang/Iterable;"],
                         [item for item in pack["policy"]["nativeMethods"]["com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient"] if item.startswith("getAt(")])
        constructors=pack["policy"]["constructors"]
        additions={key:value for key,value in constructors.items() if key not in base["policy"]["constructors"]}
        self.assertEqual({
            "cam72cam.mod.serialization.TagCompound":["()V", "(Lnet/minecraft/nbt/NBTTagCompound;)V", "([B)V"],
            "cam72cam.mod.item.ItemStack":["(Lcam72cam/mod/item/CustomItem;I)V", "(Lnet/minecraft/item/ItemStack;)V",
                                          "(Lcam72cam/mod/serialization/TagCompound;)V", "(Ljava/lang/String;II)V"],
            "supersymmetry.api.event.MobHordeEvent":[
                "(Ljava/util/function/Function;IILjava/lang/String;)V",
                "(Ljava/util/function/Function;IILjava/lang/String;I)V"],
            "gregtech.api.recipes.ingredients.GTRecipeItemInput":[
                "(Lnet/minecraft/item/ItemStack;)V", "(Lnet/minecraft/item/ItemStack;I)V",
                "([Lnet/minecraft/item/ItemStack;)V", "([Lnet/minecraft/item/ItemStack;I)V",
                "(Lgregtech/api/recipes/ingredients/GTRecipeInput;)V",
                "(Lgregtech/api/recipes/ingredients/GTRecipeInput;I)V"],
            "gregtech.api.unification.stack.MaterialStack":["(Lgregtech/api/unification/material/Material;J)V"],
            "it.unimi.dsi.fastutil.objects.Object2LongOpenHashMap":[
                "()V", "(I)V", "(IF)V", "(Ljava/util/Map;)V", "(Ljava/util/Map;F)V",
                "(Lit/unimi/dsi/fastutil/objects/Object2LongMap;)V",
                "(Lit/unimi/dsi/fastutil/objects/Object2LongMap;F)V",
                "([Ljava/lang/Object;[J)V", "([Ljava/lang/Object;[JF)V"],
            "gregtech.integration.baubles.BaubleBehavior":["(Lbaubles/api/BaubleType;)V"],
            "net.minecraft.nbt.NBTTagCompound":["()V"],
            "supersymmetry.api.unification.material.properties.FiberProperty":["()V","(ZZZ)V"],
            "supersymmetry.api.unification.material.properties.MillBallProperty":["(I)V"],
            "supersymmetry.api.unification.material.properties.DummyABSProperty":["()V","(I)V"],
            "supercritical.api.unification.material.properties.CoolantProperty":[
                "(Lgregtech/api/unification/material/Material;Lgregtech/api/unification/material/Material;Lgregtech/api/fluids/store/FluidStorageKey;DDDDD)V"]
        },additions)
        for key in ("constructors","nativeMethods"):
            for owner,signatures in base["policy"][key].items():
                additions = {
                    "java.lang.String": ["replace(CC)Ljava/lang/String;", "replace(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Ljava/lang/String;",
                        "split(Ljava/lang/String;)[Ljava/lang/String;", "split(Ljava/lang/String;I)[Ljava/lang/String;",
                        "substring(I)Ljava/lang/String;", "substring(II)Ljava/lang/String;",
                        "toUpperCase()Ljava/lang/String;", "toUpperCase(Ljava/util/Locale;)Ljava/lang/String;",
                        "replaceAll(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;"],
                    "java.util.ArrayList": ["clear()V", "contains(Ljava/lang/Object;)Z",
                        "indexOf(Ljava/lang/Object;)I", "addAll(Ljava/util/Collection;)Z", "addAll(ILjava/util/Collection;)Z", "isEmpty()Z"],
                    "gregtech.api.recipes.RecipeMap": ["getRecipeList()Ljava/util/Collection;",
                        "recipeBuilder()Lgregtech/api/recipes/RecipeBuilder;", "removeRecipe(Lgregtech/api/recipes/Recipe;)Z",
                        "findRecipe(JLnet/minecraftforge/items/IItemHandlerModifiable;Lgregtech/api/capability/IMultipleTankHandler;)Lgregtech/api/recipes/Recipe;",
                        "findRecipe(JLjava/util/List;Ljava/util/List;)Lgregtech/api/recipes/Recipe;",
                        "findRecipe(JLjava/util/List;Ljava/util/List;Z)Lgregtech/api/recipes/Recipe;",
                        "setSlotOverlay(ZZLgregtech/api/gui/resources/TextureArea;)Lgregtech/api/recipes/RecipeMap;",
                        "setSlotOverlay(ZZZLgregtech/api/gui/resources/TextureArea;)Lgregtech/api/recipes/RecipeMap;"],
                } if key == "nativeMethods" else {}
                self.assertEqual(signatures + additions.get(owner, []),pack["policy"][key][owner])
        self.assertEqual({**base["policy"]["nativeExtensions"],"java.lang.String":[
            "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
            "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;",
            "org/codehaus/groovy/runtime/StringGroovyMethods#capitalize(Ljava/lang/CharSequence;)Ljava/lang/String;"
        ],"java.lang.Double": [
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#toInteger(Ljava/lang/Number;)Ljava/lang/Integer;"
        ],"java.util.ArrayList": [
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#leftShift(Ljava/util/Collection;Ljava/lang/Object;)Ljava/util/Collection;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#leftShift(Ljava/util/List;Ljava/lang/Object;)Ljava/util/List;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#multiply(Ljava/lang/Iterable;Ljava/lang/Number;)Ljava/util/Collection;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#multiply(Ljava/util/List;Ljava/lang/Number;)Ljava/util/List;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#combinations(Ljava/lang/Iterable;)Ljava/util/List;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#combinations(Ljava/lang/Iterable;Lgroovy/lang/Closure;)Ljava/util/List;"
        ],"it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap$MapEntrySet": [
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#toList(Ljava/lang/Iterable;)Ljava/util/List;"
        ],"it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap": [
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#get(Ljava/util/Map;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/util/Map;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;"
        ],"gregtech.api.unification.material.Material": [
            "gregtech/integration/groovy/MaterialPropertyExpansion#addIngot(Lgregtech/api/unification/material/Material;)V",
            "supersymmetry/integration/groovyscript/SuSyExpansions#setBaseProof(Lgregtech/api/unification/material/Material;Z)V"
        ],"gregtech.api.fluids.FluidBuilder": [
            "gregtech/integration/groovy/GroovyExpansions#acidic(Lgregtech/api/fluids/FluidBuilder;)Lgregtech/api/fluids/FluidBuilder;",
            "supersymmetry/integration/groovyscript/SuSyExpansions#basic(Lgregtech/api/fluids/FluidBuilder;)Lgregtech/api/fluids/FluidBuilder;"
        ],"com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient": [
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#first(Ljava/lang/Iterable;)Ljava/lang/Object;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#each(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;",
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#each(Ljava/lang/Iterable;Lgroovy/lang/Closure;)Ljava/lang/Iterable;"
        ], **{owner: [
            "dev/tianmi/sussypatches/integration/grs/GroovyExpansions#info(Lgregtech/api/recipes/RecipeBuilder;Ljava/lang/String;[Ljava/lang/Object;)Lgregtech/api/recipes/RecipeBuilder;"
        ] + (["org/codehaus/groovy/runtime/DefaultGroovyMethods#tap(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;"]
             if owner in ("gregtech.api.recipes.builders.SimpleRecipeBuilder", "supersymmetry.api.recipes.builders.CatalystRecipeBuilder",
                          "gregtech.api.recipes.builders.PrimitiveRecipeBuilder") else [])
          for owner in ("supersymmetry.api.recipes.builders.SusyRecipeBuilder",
            "gregtech.api.recipes.builders.SimpleRecipeBuilder", "gregtech.api.recipes.builders.FuelRecipeBuilder",
            "supersymmetry.api.recipes.builders.NoEnergyRecipeBuilder",
            "gregtech.api.recipes.builders.PrimitiveRecipeBuilder",
            "supersymmetry.api.recipes.builders.PseudoMultiRecipeBuilder",
            "supersymmetry.api.recipes.builders.CatalystRecipeBuilder",
        "gregtech.api.recipes.builders.AssemblerRecipeBuilder")},
        "net.minecraft.nbt.NBTTagCompound":["org/codehaus/groovy/runtime/DefaultGroovyMethods#tap(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;"]}, {**pack["policy"]["nativeExtensions"],
             "gregtech.api.unification.material.Material":pack["policy"]["nativeExtensions"]["gregtech.api.unification.material.Material"][:2]})
        self.assertEqual({"gregtech.api.fluids.FluidBuilder":["temperature:I"],
                          "gregtech.api.unification.stack.MaterialStack":["material:Lgregtech/api/unification/material/Material;", "amount:J"],
                          "gregtech.api.unification.ore.OrePrefix":["secondaryMaterials:Ljava/util/List;"],
                          "net.minecraft.init.Blocks":["LOG:Lnet/minecraft/block/Block;", "LOG2:Lnet/minecraft/block/Block;", "NETHERRACK:Lnet/minecraft/block/Block;"],
                          "gregtechfoodoption.worldgen.trees.GTFOTrees":["RAINBOWWOOD_TREE:Lgregtechfoodoption/worldgen/trees/RainbowwoodTree;"],
                          "gregtechfoodoption.worldgen.trees.GTFOTree":["logState:Lnet/minecraft/block/state/IBlockState;"],
                          "gregtechfoodoption.worldgen.trees.RainbowwoodTree":["logState:Lnet/minecraft/block/state/IBlockState;"],
                          "net.minecraft.block.BlockLog":["LOG_AXIS:Lnet/minecraft/block/properties/PropertyEnum;"],
                          "java.lang.System":["out:Ljava/io/PrintStream;"],
                          "cam72cam.mod.item.ItemStack":["internal:Lnet/minecraft/item/ItemStack;"],
                          "cam72cam.immersiverailroading.IRItems":["ITEM_ROLLING_STOCK:Lcam72cam/immersiverailroading/items/ItemRollingStock;"],
                          "trackapi.lib.Gauges":["STANDARD:D"],
                          "gregtech.api.recipes.ingredients.nbtmatch.NBTMatcher":["ANY:Lgregtech/api/recipes/ingredients/nbtmatch/NBTMatcher;"],
                          "gregtech.api.recipes.ingredients.nbtmatch.NBTCondition":["ANY:Lgregtech/api/recipes/ingredients/nbtmatch/NBTCondition;"],
                          "gregtechfoodoption.item.GTFOMetaItem":[name+":Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;" for name in (
                              "RAW_DITALINI", "DRIED_DITALINI", "RAW_RIGATONI", "DRIED_RIGATONI",
                              "RAW_LASAGNA", "DRIED_LASAGNA", "RAW_SPAGHETTI", "DRIED_SPAGHETTI",
                              "RAW_TAGLIATELLE", "DRIED_TAGLIATELLE")]},pack["policy"]["nativeReadFields"])
        self.assertEqual({"supersymmetry.common.tileentities.TileEntityFlare":["callGroovySpawn"]},pack["policy"]["nativeMetaClassMethods"])
        self.assertEqual({"net.minecraft.block.Block":["blockHardness:F"],
                          "net.minecraft.block.BlockNetherrack":["blockHardness:F"]},pack["policy"]["nativeWriteFields"])
        self.assertEqual([*base["policy"]["staticFields"], "supersymmetry.common.materials.SusyMaterials",
                          "gregtech.api.recipes.RecipeMaps", "supersymmetry.api.recipes.SuSyRecipeMaps", "baubles.api.BaubleType",
                          "gregicality.multiblocks.api.recipes.GCYMRecipeMaps", "gregtechfoodoption.recipe.GTFORecipeMaps",
                          "gregtech.api.gui.GuiTextures", "supersymmetry.api.gui.SusyGuiTextures", "java.lang.Integer",
                          "supersymmetry.common.blocks.SuSyBlocks", "gregtech.common.blocks.MetaBlocks",
                          "supersymmetry.common.blocks.SusyStoneVariantBlock$StoneVariant", "gregtech.common.blocks.StoneVariantBlock$StoneVariant",
                          "supersymmetry.common.blocks.SusyStoneVariantBlock$StoneType", "gregtech.common.blocks.StoneVariantBlock$StoneType",
                          "gregtech.api.metatileentity.multiblock.CleanroomType",
                          "gregtech.api.capability.GregtechCapabilities", "net.minecraft.block.BlockLog$EnumAxis"], pack["policy"]["staticFields"])
        self.assertEqual(["material","metaitem","item","ore","fluid","liquid","recipemap"],pack["policy"]["nativeObjectMappers"])
        bound=module.bind_material_admission((root/"axiom-material-admission.json").read_bytes(),context)
        self.assertEqual(sha256(bound).hexdigest(),pack["sha256"])
        selected,=[row for row in catalog["contexts"] if row["id"]==context]
        self.assertEqual("recipes", selected["initializationStage"])
        self.assertTrue(selected["excludedComposition"])
        self.assertFalse(selected["wholePackParity"])
        self.assertEqual("pending-native-initialization-evidence",selected["qualification"])

    def test_recipe_admission_remains_pack_owned_and_does_not_open_compiler_dispatch(self):
        root,module=self.profile_extension()
        with patch.object(module,"files",return_value=root):
            pack=module.material_admission("supersymmetry:material-authoring-pack")["policy"]
            base=module.material_admission("supersymmetry:material-authoring-gt-base")["policy"]
        self.assertEqual([],base["nativeObjectMappers"])
        self.assertNotIn("resource",pack["nativeObjectMappers"])
        self.assertEqual(["boolean", "[I"],pack["referenceCasts"]["java.util.ArrayList"])
        self.assertEqual("func_74783_a",pack["nativeMethodMappings"]["net.minecraft.nbt.NBTTagCompound"]["setIntArray"])
        self.assertIn("func_74783_a(Ljava/lang/String;[I)V",pack["nativeMethods"]["net.minecraft.nbt.NBTTagCompound"])
        self.assertEqual(["org/codehaus/groovy/runtime/DefaultGroovyMethods#tap(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;"],
                         pack["nativeExtensions"]["net.minecraft.nbt.NBTTagCompound"])
        self.assertNotIn("java.util.ArrayList",base["referenceCasts"])
        self.assertEqual(["collect"],pack["listOperations"]["net.minecraft.util.NonNullList"])
        self.assertNotIn("org/codehaus/groovy/runtime/typehandling/DefaultTypeTransformation#booleanUnbox(Ljava/lang/Object;)Z",
                         pack["compilerStaticCalls"])
        methods=pack["nativeMethods"]
        self.assertEqual(["multiply(Ljava/lang/Number;)Lcom/cleanroommc/groovyscript/api/IResourceStack;",
                          "getFluid()Lnet/minecraftforge/fluids/Fluid;"], methods["net.minecraftforge.fluids.FluidStack"])
        self.assertEqual(["addRecyclingGroovy(Lnet/minecraft/item/ItemStack;Ljava/util/List;)V"],
                         methods["supersymmetry.loaders.recipes.handlers.RecyclingManager"])
        self.assertIn("removeRecipe(Lgregtech/api/recipes/Recipe;)Z",methods["gregtech.api.recipes.RecipeMap"])
        self.assertNotIn("removeAllRecipes()V",methods["gregtech.api.recipes.RecipeMap"])
        self.assertEqual(["multiply(Ljava/lang/Number;)Lcom/cleanroommc/groovyscript/api/IResourceStack;",
                          "reuse()Lnet/minecraft/item/ItemStack;", "noReturn()Lnet/minecraft/item/ItemStack;",
                          "transform(Lcom/cleanroommc/groovyscript/compat/vanilla/ItemStackTransformer;)Lnet/minecraft/item/ItemStack;",
                          "transform(Lnet/minecraft/item/ItemStack;)Lnet/minecraft/item/ItemStack;",
                          "withNbt(Lnet/minecraft/nbt/NBTTagCompound;)Lcom/cleanroommc/groovyscript/api/INBTResourceStack;",
                          "withNbt(Ljava/util/Map;)Lcom/cleanroommc/groovyscript/api/INBTResourceStack;",
                          "withAmount(I)Lcom/cleanroommc/groovyscript/api/IIngredient;",
                          "withAmount(I)Lcom/cleanroommc/groovyscript/api/IResourceStack;",
                          "getAmount()I", "getMatchingStacks()[Lnet/minecraft/item/ItemStack;", "isEmpty()Z",
                          "getCapability(Lnet/minecraftforge/common/capabilities/Capability;Lnet/minecraft/util/EnumFacing;)Ljava/lang/Object;",
                          "mark(Ljava/lang/String;)Lcom/cleanroommc/groovyscript/api/IMarkable;",
                          "func_77946_l()Lnet/minecraft/item/ItemStack;",
                          "func_77978_p()Lnet/minecraft/nbt/NBTTagCompound;",
                          "func_77982_d(Lnet/minecraft/nbt/NBTTagCompound;)V",
                          "func_77960_j()I",
                          "func_77973_b()Lnet/minecraft/item/Item;",
                          "or(Lcom/cleanroommc/groovyscript/api/IIngredient;)Lcom/cleanroommc/groovyscript/api/IIngredient;",
                          "or(Ljava/util/function/Predicate;)Ljava/util/function/Predicate;"],
                         methods["net.minecraft.item.ItemStack"])
        self.assertNotIn("org/codehaus/groovy/runtime/ScriptBytecodeAdapter#createRange(Ljava/lang/Object;Ljava/lang/Object;ZZ)Ljava/util/List;",
                         pack["compilerStaticCalls"])
        self.assertEqual(["removeAllRecipes(Lgregtech/api/recipes/RecipeMap;)V"], methods["gregtech.api.recipes.GTRecipeHandler"])

    def setUp(self):
        self.lock = json.loads(source.LOCK.read_bytes())
        self.roots = {name: Path(name) for name in source.PATHS}
        self.expected = {(r, p) for r, paths in source.PATHS.items() for p in paths}

    def test_fission_factory_and_scalar_builder_are_pack_only_without_later_callbacks(self):
        root,module=self.profile_extension()
        with patch.object(module,"files",return_value=root):
            pack=module.material_admission("supersymmetry:material-authoring-pack")["policy"]
            base=module.material_admission("supersymmetry:material-authoring-gt-base")["policy"]
        fuel="supercritical.api.unification.material.properties.FissionFuelProperty"
        builder=fuel+"$FissionFuelPropertyBuilder"
        self.assertEqual(["builder(Ljava/lang/String;IID)L"+builder.replace(".","/")+";"],pack["nativeMethods"][fuel])
        self.assertEqual(13,len(pack["nativeMethods"][builder]))
        self.assertNotIn(fuel,pack["constructors"])
        self.assertNotIn(builder,pack["constructors"])
        self.assertNotIn(fuel,base["nativeMethods"])
        for name in ("depletedFuelSupplier","allDepletedFuels","getDepletedFuel","getDepletedFuels"):
            self.assertFalse(any(name in signature for owner in (fuel,builder) for signature in pack["nativeMethods"][owner]))

    def test_inventory_matches_selected_immutable_authorities(self):
        self.assertEqual(source.selected_revisions(), self.lock["revisions"])
        rows = self.lock["references"]
        self.assertEqual(self.expected, {(r["repository"], r["path"]) for r in rows})
        self.assertEqual(len(self.expected), len(rows))
        for row in rows:
            self.assertGreater(row["size"], 0)
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["gitBlob"], r"^[0-9a-f]{40}$")

    def test_source_byte_drift_rejects(self):
        with patch.object(source, "git", return_value=b"changed source"):
            with self.assertRaisesRegex(ValueError, "identity differs"):
                source.verify_sources(self.roots, self.lock)

    def test_revision_drift_rejects_before_reading_source(self):
        self.lock["revisions"]["groovyscript"] = "0" * 40
        with patch.object(source, "git") as read:
            with self.assertRaisesRegex(ValueError, "revision differs"):
                source.verify_sources(self.roots, self.lock)
            read.assert_not_called()

    def test_complete_inventory_is_read_from_selected_objects_not_head(self):
        for row in self.lock["references"]:
            row.update(source.source_identity(b"qualified test input"))
        with patch.object(source, "git", return_value=b"qualified test input") as read:
            self.assertEqual(self.expected, set(source.verify_sources(self.roots, self.lock)))
            for call, row in zip(read.call_args_list, self.lock["references"]):
                self.assertEqual((self.roots[row["repository"]], "show",
                                  self.lock["revisions"][row["repository"]] + ":" + row["path"]), call.args)

    def test_missing_duplicate_unknown_and_unsafe_source_reject(self):
        fake = deepcopy(self.lock)
        for row in fake["references"]:
            row.update(source.source_identity(b"x"))
        variants = []
        missing = deepcopy(fake); missing["references"].pop(); variants.append(missing)
        duplicate = deepcopy(fake); duplicate["references"].append(duplicate["references"][0]); variants.append(duplicate)
        unknown = deepcopy(fake); unknown["references"][0]["path"] = "unselected.java"; variants.append(unknown)
        unsafe = deepcopy(fake); unsafe["references"][0]["path"] = "../escape.java"; variants.append(unsafe)
        with patch.object(source, "git", return_value=b"x"):
            for candidate in variants:
                with self.subTest(candidate=candidate["references"][0]["path"]):
                    with self.assertRaises(ValueError):
                        source.verify_sources(self.roots, candidate)

    def test_complete_program_keeps_class_helper_binding_and_event_inputs(self):
        root = ROOT / "modules/axiom/tests/fixtures/material-program"
        config = json.loads((root / "groovy/runConfig.json").read_bytes())
        self.assertEqual(["classes/", "globals/", "material/", "preInit/"], config["loaders"]["preInit"])
        producer = (root / "groovy/material/DeveloperMaterials.groovy").read_text()
        edits = (root / "groovy/classes/MaterialEdits.groovy").read_text()
        listeners = (root / "groovy/preInit/Materials.groovy").read_text()
        for expression in ("log.infoMC", "static Material.Builder named", "Silicon * 4", "Aluminosilicate = named", "Phosphate = named", "Titanate = named"):
            self.assertIn(expression, producer)
        self.assertIn("setFormula", edits)
        self.assertIn("Titanate.addFlags", edits)
        self.assertIn("MaterialEvent event", listeners)
        self.assertIn("PostMaterialEvent event", listeners)
        expectations = json.loads((root / "expectations.json").read_bytes())
        self.assertEqual(3, len(expectations["materials"]))
        self.assertFalse(expectations["wholePackParity"])
        self.assertEqual(0x99ccbb, expectations["materials"][0]["color"])

    def test_profile_context_is_explicit_pending_and_packaged(self):
        root = ROOT / "profiles/packs/supersymmetry"
        policy = json.loads((root / "src/workbench_profile_supersymmetry/axiom-material-contexts.json").read_bytes())
        self.assertEqual("supersymmetry", policy["profile"])
        context, = [row for row in policy["contexts"] if row['id']=="supersymmetry:material-authoring-gt-base"]
        self.assertEqual("pending-native-groovy-execution", context["qualification"])
        self.assertEqual(["gregtech"], context["materialRegistryOwners"])
        self.assertFalse(context["wholePackParity"])
        self.assertFalse(context["gameLaunchRequired"])
        self.assertEqual("cleanroom", context["platformProfile"])
        admission_path=root / "src/workbench_profile_supersymmetry" / context["admissionPolicy"]
        admission=json.loads(admission_path.read_bytes())
        self.assertEqual(context["id"],admission["context"])
        self.assertEqual(policy["profile"],admission["profile"])
        self.assertEqual("pending-native-admission",admission["qualification"])
        self.assertIn('"axiom-material-admission.json"',(root / "pyproject.toml").read_text())
        for owner, revision in context["sourceRevisions"].items():
            self.assertEqual(source.selected_revisions()[owner], revision)
        self.assertIn('"axiom-material-contexts.json"', (root / "pyproject.toml").read_text())

    def test_source_witnesses_retain_whole_program_and_exact_base(self):
        cases = corpus.cases()
        self.assertEqual(9, len(cases))
        base = cases[0]
        identities = set()
        for case in cases:
            self.assertEqual(set(base["files"]), set(case["files"]))
            self.assertEqual(base["baseIdentity"], case["baseIdentity"])
            self.assertEqual(corpus.identity(case["files"]), case["candidateIdentity"])
            self.assertFalse(case["executionQualified"])
            identities.add(case["candidateIdentity"]["sha256"])
            changes = [p for p in base["files"] if base["files"][p] != case["files"][p]]
            self.assertEqual(0 if case is base else 1, len(changes))
        self.assertEqual(len(cases), len(identities))

    def test_source_edits_fail_when_anchor_is_missing_or_ambiguous(self):
        edit = corpus.Edit("a.groovy", "needle", "changed")
        for raw in (b"unrelated", b"needle needle"):
            with self.assertRaisesRegex(ValueError, "one exact source anchor"):
                edit.apply({"a.groovy": raw})

    def test_corpus_inventory_cannot_silently_expand_or_drop_sources(self):
        from tempfile import TemporaryDirectory
        import shutil
        with TemporaryDirectory() as temp:
            root = Path(temp) / "program"
            shutil.copytree(corpus.FIXTURE, root)
            (root / "groovy/unlisted.groovy").write_text("throw new Error()")
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                corpus.cases(root)


class MaterialObservationReferenceTests(unittest.TestCase):
    """Test reference construction/comparison, not native execution or parity."""
    def test_original_registration_section_is_verbatim_and_unique(self):
        from axiom_material_observation_conformance import registration_source
        section = '/* Start Material Registration */\n first();\n second();\n/* End Material Registration */'
        wrapped, observed = registration_source('before();\n' + section + '\nafter();')
        self.assertEqual(section, observed)
        self.assertIn(section, wrapped)
        self.assertNotIn('before();', wrapped)
        self.assertNotIn('after();', wrapped)
        for invalid in ('unrelated', section + section, '/* End Material Registration *//* Start Material Registration */'):
            with self.assertRaisesRegex(ValueError, 'unique and ordered'):
                registration_source(invalid)

    def test_reference_corpus_keeps_complete_programs_and_native_helpers(self):
        from axiom_material_observation_conformance import corpus as reference_corpus
        selected = reference_corpus()
        self.assertEqual(9, len(selected))
        base = corpus.cases()[0]['files']
        for case in selected:
            self.assertEqual(set(base), set(case['files']))
            self.assertEqual(base['groovy/runConfig.json'], case['files']['groovy/runConfig.json'])
        helper = next(case for case in selected if case['name'] == 'overloaded-side-effecting-helper')
        self.assertIn(b'colorValue(int value)', helper['files'][corpus.EDITS])
        self.assertIn(b'colorValue(String value)', helper['files'][corpus.EDITS])
        self.assertIn(b'evaluated = evaluated + 1', helper['files'][corpus.EDITS])
        nested=next(case for case in selected if case['name']=='nested-deferred-property-error')
        self.assertIn(b'static void nestedEdit() { invalidProperty() }',nested['files'][corpus.EDITS])
        self.assertTrue(nested['nativeError']);self.assertFalse(nested['executionCompleted'])
        self.assertNotIn('timeout', {case['name'] for case in selected})

    def observations(self):
        reference = {'phase': 'FROZEN', 'registeredMaterials': 605, 'executionCompleted': True, 'coverageGaps': [], 'nativeErrors': [],
            'materials': [], 'activeOwner': 'gregtech', 'contentPhase': 'COMPLETE',
            'counts': dict.fromkeys(('variants', 'registeredBlocks', 'registeredBlockItems', 'registeredOreBlocks', 'registeredOreItems'), 0),
            'prefixQueues': {}, 'fluids': []}
        native = {key: deepcopy(reference[key]) for key in ('phase', 'registeredMaterials', 'executionCompleted', 'coverageGaps', 'nativeErrors', 'materials')}
        native.update(lifecycle={'checkpoints': [{'activeOwner': 'gregtech'}]},
            contentProgress={'phase': 'COMPLETE', 'completedCheckpoints': [dict(reference['counts'])]},
            deferredWork={'prefixProcessing': [], 'fluids': []})
        return reference, native

    def test_missing_queues_counts_and_owner_do_not_compare_equal_to_empty(self):
        from axiom_material_observation_conformance import compare
        reference, native = self.observations()
        self.assertEqual([], compare(reference, native))
        native['deferredWork'].pop('prefixProcessing')
        self.assertIn('prefixQueues', compare(reference, native))
        native['contentProgress']['completedCheckpoints'] = []
        self.assertIn('counts', compare(reference, native))
        native['lifecycle']['checkpoints'] = []
        self.assertIn('activeOwner', compare(reference, native))

    def test_native_source_frame_order_is_not_normalized(self):
        from axiom_material_observation_conformance import compare
        reference, native = self.observations()
        frames = [{'path': corpus.EDITS, 'line': line, 'class': 'classes.MaterialEdits', 'method': 'apply'} for line in (8, 10)]
        reference['failure'] = {'type': 'java.lang.IllegalArgumentException', 'message': 'native failure', 'frames': frames}
        native['nativeException'] = 'java.lang.IllegalArgumentException: native failure\ntrace'
        native['nativeSourceFrames'] = frames
        native['diagnostics'] = [{'channel': 'native-exception', 'locations': frames}]
        self.assertEqual([], compare(reference, native))
        native['diagnostics'][0]['locations'] = list(reversed(frames))
        self.assertIn('sourceFrames', compare(reference, native))
        native['nativeException'] = 'java.lang.IllegalStateException: native failure\ntrace'
        self.assertIn('failure', compare(reference, native))

    def test_generated_native_frame_is_retained_without_an_editor_location(self):
        from axiom_material_observation_conformance import compare
        reference,native=self.observations()
        generated={'path':corpus.EDITS,'line':-1,'class':'classes.MaterialEdits$Reagent','method':'getAt'}
        caller={'path':corpus.EDITS,'line':10,'class':'classes.MaterialEdits','method':'apply'}
        reference['failure']={'type':'java.lang.IllegalArgumentException','message':'native failure','frames':[generated,caller]}
        native['nativeException']='java.lang.IllegalArgumentException: native failure\ntrace'
        native['nativeSourceFrames']=[generated,caller]
        native['diagnostics']=[{'channel':'native-exception','locations':[caller]}]
        self.assertEqual([],compare(reference,native))
        native['nativeSourceFrames']=[caller]
        self.assertIn('sourceFrames',compare(reference,native))


if __name__ == "__main__":
    unittest.main()
