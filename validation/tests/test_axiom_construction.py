"""Material construction source custody and qualification boundaries."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_construction_sources as extraction
import axiom_construction_conformance as conformance


class AxiomConstructionTests(unittest.TestCase):
    def test_source_lock_has_exact_complete_dependency_set(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        rows = lock["references"]
        self.assertEqual(set(conformance.PATHS.values()) | set(extraction.PRODUCER_PATHS.values()), {r["path"] for r in rows})
        self.assertEqual(len(rows), len({r["path"] for r in rows}))
        for row in rows:
            self.assertEqual("gtceu", row["repository"])
            self.assertEqual(64, len(row["sha256"]))
            self.assertEqual(40, len(row["gitBlob"]))

    def test_immutable_source_read_rejects_mutation_and_wrong_revision(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        with patch.object(conformance, "git", return_value=b"mutated") as read:
            with self.assertRaisesRegex(ValueError, "identity differs"):
                conformance.verify_references(Path("test"), lock)
        self.assertEqual("show", read.call_args.args[1])
        self.assertTrue(read.call_args.args[2].startswith(lock["revisions"]["gtceu"] + ":"))
        lock["revisions"]["gtceu"] = "HEAD"
        with self.assertRaisesRegex(ValueError, "revision"):
            conformance.verify_references(Path("test"), lock)

    def test_retained_mutation_is_not_resealed_as_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "Example.java"; path.write_text("same")
            with patch.object(conformance, "PATHS", {"Example": "source"}), patch.object(conformance, "extracted", return_value={"Example": "same"}):
                self.assertEqual({"Example": "same"}, conformance.retained_inputs({}, root))
                path.write_text("different")
                with self.assertRaisesRegex(ValueError, "differs"):
                    conformance.retained_inputs({}, root)

    def test_edit_witness_changes_only_the_selected_declaration(self):
        path = extraction.PRODUCER_PATHS["elements"]
        source = 'Aluminium = new Material.Builder(2, gregtechId("aluminium")).color(0x80C8F0).build();\nOther = new Material.Builder(3,null).color(0x80C8F0).build();'
        edited = conformance.edited_producer({path: source, "elsewhere": "unchanged"})
        self.assertEqual(source.replace(".color(0x80C8F0)", ".color(0x80C8F1)", 1), edited[path])
        self.assertEqual("unchanged", edited["elsewhere"])
        with self.assertRaisesRegex(ValueError, "boundary"):
            conformance.edited_producer({path: source + source})

    def test_selected_collection_pair_library_matches_build_lock(self):
        policy = json.loads((ROOT / "profiles/platforms/cleanroom/registry-runtime.json").read_bytes())
        row = next(r for r in policy["runtimeFiles"] if "commons-lang3" in r["path"])
        build = (ROOT / "modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('implementation("org.apache.commons:commons-lang3:3.20.0") { isTransitive = false }', build)
        metadata = (ROOT / "modules/axiom/jvm/gradle/verification-metadata.xml").read_text()
        self.assertIn(row["sha256"], metadata)
        self.assertIn('from("../sources/material-construction.lock.json")', build)

    def test_unadmitted_registry_effects_do_not_have_empty_success_defaults(self):
        source = (conformance.MATERIAL_ROOT / "ConstructionDependencies.java").read_text()
        wood = (conformance.MATERIAL_ROOT / "WoodProperty.java").read_text()
        self.assertIn("PrefixDependencies.requireInputs()", wood)
        environment = (conformance.MATERIAL_ROOT / "FluidEnvironment.java").read_text()
        self.assertIn('throw new Failure("incomplete", "material.ore-prefix"', environment)
        self.assertNotIn("new HashMap", source)
        self.assertIn('"rk", "a"', source)

    def test_required_ci_keeps_construction_qualification(self):
        self.assertIn("python3 tools/axiom_construction_conformance.py", (ROOT / ".github/workflows/validate.yml").read_text())
