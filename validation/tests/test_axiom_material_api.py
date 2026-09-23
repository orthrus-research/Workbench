"""Fast custody/shape checks; native Java and Groovy qualification are separate."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import axiom_material_api_sources as source
import build_axiom_material_api as build


class MaterialApiSourceTests(unittest.TestCase):
    def test_original_catalog_configuration_and_blast_owners_cannot_be_reduced(self):
        build.check_native_overrides(['gregtech/api/recipes/ModHandler.class', 'gregtech/api/unification/material/Material.class'])
        for owner in ('gregtech/api/GTValues', 'gregtech/common/ConfigHolder',
                      'gregtech/api/unification/material/properties/BlastProperty', 'gregtech/api/recipes/RecipeMaps'):
            for suffix in ('.class', '$Builder.class'):
                with self.assertRaises(ValueError):build.check_native_overrides([owner + suffix])

    def test_inventory_preserves_native_identity_and_complete_producers(self):
        inventory = (*source.CLASSES, *source.SUPPORT)
        self.assertEqual(len(inventory), len(set(inventory)))
        for path in ('api/unification/material/Material', 'api/unification/stack/MaterialStack',
                     'api/unification/material/properties/FluidProperty', 'api/capability/IPropertyFluidFilter',
                     'api/fluids/store/FluidStorage', 'api/util/FluidTooltipUtil'):
            self.assertIn(path, source.CLASSES)
        self.assertEqual(7, len(source.PRODUCERS))
        for name in source.PRODUCERS:
            self.assertIn('api/unification/material/materials/'+name, source.CLASSES)
        self.assertFalse(any('MaterialState' in name or 'FluidMaterial' in name for name in inventory))

    def test_sources_are_read_from_selected_git_objects_not_worktree(self):
        gt, groovy = Path('gtceu'), Path('groovyscript')
        with patch.object(source, 'git', return_value=b'upstream bytes') as read:
            originals, mappings = source.read_sources(gt, groovy)
        self.assertEqual(set(source.CLASSES + source.SUPPORT) | {'resource:'+p for p in source.RESOURCES}, set(originals))
        self.assertEqual('upstream bytes', mappings)
        revisions = source.selected_revisions()
        expected = [(gt,'show',revisions['gtceu']+':'+source.GT+p+'.java') for p in (*source.CLASSES,*source.SUPPORT)]
        expected.extend((gt,'show',revisions['gtceu']+':'+p) for p in source.RESOURCES)
        expected.append((groovy,'show',revisions['groovyscript']+':'+source.MAPPINGS))
        self.assertEqual(expected, [call.args for call in read.call_args_list])

    def test_annotation_removal_keeps_runtime_and_groovy_annotations(self):
        text = ('import org.jetbrains.annotations.Nullable;\n'
                'import crafttweaker.annotations.ZenRegister;\n'
                'import net.minecraftforge.fml.relauncher.SideOnly;\n'
                '@Nullable @ZenMethod @SideOnly(Side.CLIENT) @GroovyBlacklist\n'
                'public Material method() { return material; }')
        generated = source.annotations(text)
        self.assertNotIn('@Nullable', generated)
        self.assertNotIn('@ZenMethod', generated)
        self.assertIn('@SideOnly(Side.CLIENT) @GroovyBlacklist', generated)
        self.assertIn('public Material method() { return material; }', generated)

    def test_missing_or_ambiguous_original_declaration_fails(self):
        for text in ('unrelated', 'public int value = 1; public int value = 2;'):
            with self.assertRaisesRegex(ValueError, 'declaration boundary differs'):
                source.declaration(text, 'public int value =')
        with self.assertRaisesRegex(ValueError, 'linkage anchor missing'):
            source.substitute('changed upstream', 'expected anchor', 'bound call')

    def test_build_refuses_existing_and_indirect_output_before_compiler(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root/'existing'; existing.mkdir()
            link = root/'link'; link.symlink_to(existing, target_is_directory=True)
            with patch.object(build, 'verify_runtime') as runtime:
                for output in (existing, link/'new'):
                    with self.assertRaises(ValueError):
                        build.build(root,root,root,root,root,output)
                runtime.assert_not_called()

    def test_recipe_binds_host_tooling_and_transitive_source_helpers(self):
        frozen = build.recipe_inputs()
        for path in ('tools/axiom_material_api_sources.py', 'tools/axiom_catalog_sources.py',
                     'tools/axiom_fluid_sources.py', 'tools/axiom_item_sources.py',
                     'tools/build_axiom_material_api.py', 'tools/build_axiom_native_materials.py',
                     'profiles/platforms/cleanroom/native-identity-runtime.json',
                     'modules/axiom/jvm/src/materialProgramTooling/java/MaterialApiCompilerView.java',
                     'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeBoundary.java'):
            self.assertIn(ROOT/path, frozen)
            self.assertEqual((ROOT/path).read_bytes(), frozen[ROOT/path])


if __name__ == '__main__': unittest.main()
