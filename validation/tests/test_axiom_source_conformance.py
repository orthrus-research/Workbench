"""Identity and no-clobber guards for the source comparison tool."""

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_source_conformance as conformance
import axiom_material_sources as materials


class SourceConformanceCustodyTests(unittest.TestCase):
    def test_material_extraction_rejects_unadmitted_classes_and_changed_boundaries(self):
        with self.assertRaisesRegex(ValueError, "unadmitted material source"):
            materials.extract("FluidProperty", "")
        with self.assertRaisesRegex(ValueError, "boundary differs"):
            materials.extract("DustProperty", "package changed;")
        source = "package gregtech.api.unification.material.properties;\npublic class DustProperty {}\n"
        extracted = materials.extract("DustProperty", source)
        self.assertIn("package research.orthrus.axiom;", extracted)
        self.assertIn("\nclass DustProperty {}", extracted)
        with self.assertRaisesRegex(ValueError, "boundary differs"):
            materials.extract("DustProperty", source + source)

    def test_material_method_extraction_rejects_missing_or_duplicate_markers(self):
        source = "boolean method() {\n        return true;\n    }\n"
        self.assertEqual(source.strip(), materials.declaration(source, "boolean method()"))
        for changed in ("", source + source):
            with self.assertRaisesRegex(ValueError, "method boundary differs"):
                materials.declaration(changed, "boolean method()")

    def test_material_carrier_edits_cannot_bypass_source_comparison(self):
        marker = "public <T extends IMaterialProperty> void setProperty("
        method = marker + ") {\n        if (!GregTechAPI.materialManager.canModifyMaterials()) throw new IllegalStateException();\n    }"
        predicate = "default boolean canModifyMaterials() {\n        return this.getPhase() != Phase.FROZEN && this.getPhase() != Phase.PRE;\n    }"
        manager = predicate + "\nenum Phase {\n        PRE,\n        OPEN,\n        CLOSED,\n        FROZEN\n    }"
        originals = {materials.MATERIAL: method, materials.MANAGER: manager}
        carrier = method.replace("GregTechAPI.materialManager.canModifyMaterials()", "canModifyMaterials.getAsBoolean()")
        phase = "PRE, OPEN, CLOSED, FROZEN;\n" + predicate.replace("default ", "").replace("this.getPhase()", "this").replace("Phase.", "MaterialPhase.")
        materials.checked_carriers(originals, carrier, phase)
        with self.assertRaisesRegex(ValueError, "mutation guard differs"):
            materials.checked_carriers(originals, carrier.replace("!canModify", "canModify"), phase)
        with self.assertRaisesRegex(ValueError, "phase predicate differs"):
            materials.checked_carriers(originals, carrier, phase.replace("&&", "||"))
        with self.assertRaisesRegex(ValueError, "phase domain differs"):
            materials.checked_carriers(originals, carrier, phase.replace("PRE, OPEN, CLOSED, FROZEN;", "PRE, CLOSED, OPEN, FROZEN;"))

    def test_builder_extraction_requires_unique_complete_method_boundaries(self):
        source = ('protected void validateGroovy(GroovyLog.Msg m) {\n        m.add(true, () -> "original");\n    }\n'
                  'protected static String getRequiredString(int max, int actual, @NotNull String type) {\n        return type;\n    }\n')
        extracted = conformance.builder_methods(source)
        self.assertIn('m.add(true, () -> "original");', extracted)
        self.assertNotIn('@NotNull', extracted)
        with self.assertRaisesRegex(ValueError, "boundary differs"):
            conformance.builder_methods(source + source)
        with self.assertRaisesRegex(ValueError, "boundary differs"):
            conformance.builder_methods(source.split('protected static')[0])

    def fixture(self, root, binding):
        (root / "lib").mkdir()
        jar = root / "lib/engine.jar"
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("axiom/supersymmetry.lock.json", binding)
        (root / "engine-manifest.json").write_text(json.dumps({"jars": {jar.name: sha256(jar.read_bytes()).hexdigest()}}))

    def test_binding_and_exact_library_inventory_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root, b"exact binding")
            raw, manifest, paths = conformance.engine_inputs(root, b"exact binding")
            self.assertEqual([root / "lib/engine.jar"], paths)
            self.assertEqual(json.loads(raw), manifest)
            with self.assertRaisesRegex(ValueError, "binding differs"):
                conformance.engine_inputs(root, b"different binding")
            (root / "lib/extra.jar").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "unlisted or missing"):
                conformance.engine_inputs(root, b"exact binding")

    def test_changed_jar_and_missing_binding_do_not_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root, b"binding")
            (root / "lib/engine.jar").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "input changed"):
                conformance.engine_inputs(root, b"binding")

    def test_locked_source_bytes_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Original.java").write_bytes(b"original")
            lock = {"references": [{"repository": "gtceu", "path": "Original.java", "sha256": sha256(b"original").hexdigest()}]}
            self.assertEqual("original", conformance.source(root, "Original.java", lock))
            (root / "Original.java").write_bytes(b"altered")
            with self.assertRaisesRegex(ValueError, "differs from the locked"):
                conformance.source(root, "Original.java", lock)

    def test_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_bytes(b"previous result")
            with self.assertRaisesRegex(ValueError, "already exists"):
                conformance.main(["--gtceu", temporary, "--java-home", temporary, "--engine-home", temporary, "--report", str(path)])
            self.assertEqual(b"previous result", path.read_bytes())
