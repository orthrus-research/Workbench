"""Native runtime selection, custody and removal of the retired JVM backend."""

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_runtime


class AxiomRuntimeTests(unittest.TestCase):
    @patch("axiom_runtime.verify_runtime", return_value={"runtimeVersion": "25.0.4"})
    @patch("axiom_runtime.provisioned_java_selection")
    def test_core_selected_java_is_exported_without_a_fixed_host_path(self, provision, verify):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "selected-java"
            provision.return_value = home
            env_file = Path(temporary) / "github-env"
            self.assertEqual(0, axiom_runtime.main([
                "--provision-java", "--github-env-file", str(env_file),
            ]))
            self.assertEqual(f"WORKBENCH_TEST_JAVA={home / 'bin/java'}\n", env_file.read_text())
            verify.assert_called_once_with(home, compiler=True)

    def test_runtime_has_one_profile_owner_and_matches_provisioning(self):
        policy = json.loads(axiom_runtime.POLICY.read_bytes())
        profile = yaml.safe_load((ROOT / "profiles/platforms/cleanroom/provisional.yaml").read_text())
        provision = json.loads((ROOT / "validation/ide-toolchains-v1.json").read_bytes())
        self.assertEqual("axiom.jvm-runtime.v1", policy["schema"])
        self.assertEqual("cleanroom", policy["profile"])
        self.assertEqual(provision["java_platform"], policy["archive"])
        self.assertEqual(profile["java"]["runtime_provision"]["java_vendor"], policy["vendor"])
        self.assertEqual(profile["java"]["runtime_provision"]["release_name"], policy["archive"]["archive_root"])
        self.assertEqual("selected-workbench-mvp-runtime", policy["selectionStatus"])
        self.assertEqual("unqualified-installed-native-runtime", policy["qualificationStatus"])
        paths = [row["path"] for row in policy["runtimeFiles"] + policy["compilerFiles"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue({"bin/java", "bin/javac", "lib/modules", "lib/server/libjvm.so", "release"}.issubset(paths))

    def test_runtime_file_custody(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "java"; path.write_bytes(b"pinned")
            row = {"path": "java", "size": 6, "sha256": sha256(b"pinned").hexdigest()}
            self.assertEqual(path, axiom_runtime.checked_path(root, row))
            path.write_bytes(b"change")
            with self.assertRaisesRegex(ValueError, "changed"):
                axiom_runtime.checked_path(root, row)
            path.unlink(); path.symlink_to(root / "elsewhere")
            with self.assertRaisesRegex(ValueError, "indirect"):
                axiom_runtime.checked_path(root, row)

    def test_unsafe_runtime_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for path in ("../java", "/bin/java", "lib/../java", "lib\\java", "", "./java"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    axiom_runtime.checked_path(Path(temporary), {"path": path})

    def test_retired_executor_cannot_be_built_or_advertised(self):
        root = ROOT / "modules/axiom/jvm/src/main/java/research/orthrus/axiom"
        for name in ("BytecodeExecution", "ClassExecution", "ClassNamespaces", "ClassInitialization", "ClassAssertions", "JarSignatures"):
            self.assertFalse((root / (name + ".java")).exists(), name)
        build = (ROOT / "modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../../../profiles/platforms/cleanroom/jvm-runtime.json")', build)
        self.assertNotIn("oracles/static-execution", build)
        self.assertNotIn("--add-opens", build)
        self.assertIn("options.release = 25", build)

    def test_retained_source_and_loader_comparisons_have_their_drivers(self):
        root = ROOT / "modules/axiom/tests/oracles"
        self.assertEqual({"SourceConformance.java", "LoaderConformance.java", "MaterialConformance.java", "RegistryConformance.java", "FluidConformance.java", "EventConformance.java", "ConstructionConformance.java", "PrefixConformance.java", "ForgeRegistryConformance.java", "FluidStackConformance.java", "NativeIdentityInputs.java", "NativeIdentityConformance.java", "NativeMaterialConformance.java", "NativeProducerConformance.java", "NativeCatalogConformance.java", "NativeCatalogProbe.java", "NativeCatalogTransformConformance.java", "NativeItemConformance.java", "NativeItemProbe.java", "NativeItemSourceConformance.java", "NativeMaterialItemConformance.java", "NativeMaterialItemProbe.java", "NativeMaterialBlockConformance.java", "NativeMaterialBlockProbe.java", "NativeMaterialOreConformance.java", "NativeMaterialOreProbe.java", "NativeOreAddonProbe.java",
                      "NativeRootStageProbe.java", "MaterialApiConformance.java", "MaterialApiProbe.java", "MaterialApiCompilerViewProbe.java",
                          "GroovyTransformConformance.java", "GroovyTransformProbe.java", "ContextMixinService.java",
                          "GroovyNativeBootstrap.java", "GroovyLanguageConformance.java", "GroovyLanguageProbe.java",
                          "NativeTransformAudit.java", "GroovyNativeObservations.java", "GroovyCandidateBytecode.java",
                          "MaterialArgumentBytecode.java", "NativeCompiledCacheProbe.java", "MaterialTraitReference.java",
                          "RecipeMapReference.java", "MaterialObservationReference.java"},
                         {path.relative_to(root).as_posix() for path in root.rglob("*.java")})
        for path in root.rglob("*.java"):
            self.assertNotIn("BytecodeExecution", path.read_text())
