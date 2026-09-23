"""Native-linked material source closure and offline build/qualification boundaries."""
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_native_material_sources as source
import axiom_native_material_conformance as qualification
import build_axiom_native_materials as build


class NativeMaterialTests(unittest.TestCase):
    def test_complete_source_closure_links_actual_native_types(self):
        files = source.assemble()
        self.assertEqual(58, len(source.NAMES)); self.assertEqual(109, len(files))
        self.assertIn("extends net.minecraftforge.fluids.Fluid", files["GTFluid"])
        self.assertIn("new net.minecraftforge.fluids.FluidStack", files["FluidMaterial"])
        for name in ("NativeFluid", "NativeFluidStack", "FluidRegistryState", "NativeLocation"):
            self.assertNotIn(name, files)
        self.assertIn("MathHelper.func_76125_a", files["ConstructionDependencies"])

    def test_no_missing_extra_or_renamed_host_is_silently_admitted(self):
        shared = {n:(source.SHARED/(n+".java")).read_text() for n in source.NAMES}
        with self.assertRaisesRegex(ValueError,"kernel closure"): source.assemble({**shared,"Unexpected":"class Unexpected {}"})
        shared.pop("FluidBuilder")
        with self.assertRaisesRegex(ValueError,"kernel closure"): source.assemble(shared)
        with self.assertRaisesRegex(ValueError,"host closure"): source.assemble(host={})
        with self.assertRaisesRegex(ValueError,"source name"): source.linked("../outside","source")

    def test_java_type_relocation_preserves_diagnostic_literals(self):
        value = source.linked("Fixture", 'package research.orthrus.axiom; class Fixture { NativeFluid f; String s = "NativeFluid"; }')
        self.assertIn('net.minecraftforge.fluids.Fluid f;', value)
        self.assertIn('"NativeFluid"', value)

    def test_changed_clamp_binding_rejects(self):
        with self.assertRaisesRegex(ValueError,"clamp binding"): source.linked("ConstructionDependencies","class ConstructionDependencies {}")

    def test_host_uses_native_maps_and_owner_not_synthetic_state(self):
        files=source.assemble()
        self.assertIn("Loader.instance().activeModContainer()",files["RegistryRuntime"])
        self.assertIn("FluidRegistry.class.getDeclaredField",files["FluidRegistryAccess"])
        self.assertNotIn("new Hash",files["FluidRegistryAccess"])
        self.assertNotIn("LoadController",files["RegistryRuntime"])
        self.assertIn("modController",qualification.FIXTURE.read_text())
        self.assertIn("Full material catalog/configuration is not bound",files["FluidEnvironment"])

    def test_program_archives_are_reproducible_and_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            files={"z.class":b"z","a.class":b"a"}
            build.jar_bytes(root/"one.jar",files); build.jar_bytes(root/"two.jar",dict(reversed(list(files.items()))))
            self.assertEqual((root/"one.jar").read_bytes(),(root/"two.jar").read_bytes())
            with zipfile.ZipFile(root/"one.jar") as jar:
                self.assertEqual(["a.class","z.class"],jar.namelist())
                self.assertTrue(all(i.date_time==(1980,1,1,0,0,0) for i in jar.infolist()))
            with self.assertRaises(FileExistsError): build.jar_bytes(root/"one.jar",files)

    def test_build_rejects_existing_and_indirect_outputs_before_compiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); link=root/"link"; link.symlink_to(root,target_is_directory=True)
            with patch.object(build,"engine_sources") as read:
                with self.assertRaisesRegex(ValueError,"must be new"): build.build(root,root,root,root,root)
                with self.assertRaisesRegex(ValueError,"indirect"): build.build(root,root,root,root,link/"out")
                read.assert_not_called()

    def test_changed_upstream_rejects_before_reconstruction(self):
        with patch.object(qualification.construction,"verify_references",side_effect=ValueError("changed upstream")):
            with self.assertRaisesRegex(ValueError,"changed upstream"): qualification.original_sources({"gtceu":Path("fixture")},{})

    def test_build_embeds_host_sources_but_not_game_or_producer_binaries(self):
        script=(ROOT/"modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("src/nativeMaterials/java")',script)
        self.assertIn('into("axiom/native-materials")',script)
        self.assertNotIn("native-materials.jar",script)
        self.assertNotIn("LockedSusyProducer",script)
        ci=(ROOT/".github/workflows/validate.yml").read_text()
        self.assertLess(ci.index("tools/axiom_native_identity_conformance.py"),ci.index("tools/build_axiom_native_materials.py"))
        self.assertIn("tools/axiom_native_material_conformance.py",ci)


if __name__ == "__main__": unittest.main()
