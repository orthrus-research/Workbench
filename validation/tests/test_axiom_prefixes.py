"""Prefix/marker source custody, dependency boundaries and comparison admission."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_prefix_conformance as conformance
import axiom_prefix_sources as extraction


class AxiomPrefixTests(unittest.TestCase):
    def test_source_lock_has_exact_dependency_closure_and_fixture_labels(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        rows = lock["references"]
        self.assertEqual(set(conformance.PATHS.values()), {r["path"] for r in rows})
        self.assertEqual(len(rows), len({r["path"] for r in rows}))
        self.assertEqual({"gtceu"}, set(lock["revisions"]))
        self.assertIn("Stone", lock["fixtureMetadata"]["materialFields"])
        self.assertGreater(lock["fixtureMetadata"]["prefixDeclarations"], 70)
        self.assertGreater(lock["fixtureMetadata"]["iconDeclarations"], 50)
        for row in rows:
            self.assertEqual("gtceu", row["repository"])
            self.assertEqual(64, len(row["sha256"]))
            self.assertEqual(40, len(row["gitBlob"]))

    def test_immutable_git_reads_reject_changed_sources_and_mutable_revisions(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        with patch.object(conformance, "git", return_value=b"changed") as read:
            with self.assertRaisesRegex(ValueError, "identity differs"):
                conformance.verify_references(Path("fixture"), lock)
        self.assertEqual("show", read.call_args.args[1])
        self.assertTrue(read.call_args.args[2].startswith(lock["revisions"]["gtceu"] + ":"))
        lock["revisions"]["gtceu"] = "HEAD"
        with self.assertRaisesRegex(ValueError, "revision"):
            conformance.verify_references(Path("fixture"), lock)

    def test_retained_edits_do_not_silently_reseal_the_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "Example.java"; path.write_text("original")
            with patch.object(conformance, "PATHS", {"Example": "source"}), patch.object(conformance, "extracted", return_value={"Example": "original"}):
                self.assertEqual({"Example": "original"}, conformance.retained_inputs({}, root))
                path.write_text("changed")
                with self.assertRaisesRegex(ValueError, "differs"):
                    conformance.retained_inputs({}, root)

    def test_source_edit_is_bounded_and_ambiguity_fails(self):
        path = conformance.PATHS["OrePrefix"]
        source = 'new OrePrefix("dustTiny", M / 9, null); new OrePrefix("dustSmall", M / 4, null);'
        result = conformance.edited_source({path: source, "elsewhere": "unchanged"})
        self.assertEqual(source.replace("M / 9", "M / 10"), result[path])
        self.assertEqual("unchanged", result["elsewhere"])
        with self.assertRaisesRegex(ValueError, "boundary"):
            conformance.edited_source({path: source + source})

    def test_inputs_are_required_not_implicit_pack_defaults(self):
        environment = (conformance.MATERIAL_ROOT / "FluidEnvironment.java").read_text()
        self.assertIn('throw new Failure("incomplete", "material.ore-prefix"', environment)
        self.assertIn("Prefix inputs are already bound", environment)
        dependency = (conformance.MATERIAL_ROOT / "PrefixDependencies.java").read_text()
        self.assertNotIn("return false", dependency)
        self.assertNotIn("return true", dependency)
        self.assertNotIn("new FluidMaterial", dependency)
        wood = (conformance.MATERIAL_ROOT / "WoodProperty.java").read_text()
        self.assertLess(wood.index("PrefixDependencies.requireInputs()"), wood.index("OrePrefix.pipeTinyFluid"))
        self.assertNotIn("OrePrefix.getPrefix(", wood)

    def test_native_dye_enum_is_hash_bound_and_has_no_copied_enum_constants(self):
        policy = json.loads((ROOT / "profiles/platforms/cleanroom/registry-runtime.json").read_bytes())
        for logical, native in (("EnumDyeColor", "ahs"), ("IStringSerializable", "ro"), ("TextFormatting", "a")):
            self.assertEqual(native, policy["classes"][logical]["name"])
            self.assertEqual(64, len(policy["classes"][logical]["sha256"]))
        source = (conformance.MATERIAL_ROOT / "NativeDyeColor.java").read_text()
        self.assertNotIn("CYAN", source)
        self.assertNotIn("SILVER", source)
        self.assertIn('runtime.utility("ahs", "m"', source)

    def test_prefix_metadata_does_not_advertise_client_or_pack_execution(self):
        prefix = (conformance.MATERIAL_ROOT / "OrePrefix.java").read_text()
        self.assertNotIn("getLocalNameForItem(", prefix)
        self.assertIn("PrefixDependencies.generateLowQualityGems()", prefix)
        self.assertIn("PrefixDependencies.allUniqueStoneTypes()", prefix)
        self.assertIn("currentMaterial.remove()", prefix)
        driver = conformance.DRIVER.read_text()
        self.assertIn("WorkerIsolation.install()", driver)
        self.assertIn('"wholePackParity", false', driver)
        self.assertIn("synthetic catalog/config inputs", driver)

    def test_build_and_ci_require_the_native_prefix_lock_and_probe(self):
        build = (ROOT / "modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../sources/native-prefixes.lock.json")', build)
        self.assertIn("python3 tools/axiom_prefix_conformance.py", (ROOT / ".github/workflows/validate.yml").read_text())
        with self.assertRaisesRegex(ValueError, "unadmitted"):
            extraction.extract("Unknown", "")
