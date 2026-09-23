"""Forge registry source custody, explicit dependency boundaries and probe admission."""
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_forge_registry_conformance as conformance
import axiom_forge_registry_sources as extraction


class AxiomForgeRegistryTests(unittest.TestCase):
    def test_source_lock_has_exact_closure_and_selected_event_revision(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        rows = lock["references"]
        self.assertEqual(set(conformance.PATHS.values()), {r["path"] for r in rows})
        self.assertEqual(15, len(rows))
        self.assertEqual(json.loads(conformance.EVENT_LOCK.read_bytes())["revision"], lock["revision"])
        for row in rows:
            self.assertEqual("cleanroom", row["repository"])
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["gitBlob"], r"^[0-9a-f]{40}$")

    def test_immutable_git_reads_reject_changed_sources_and_mutable_revisions(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        with patch.object(conformance, "git", return_value=b"changed") as read:
            with self.assertRaisesRegex(ValueError, "identity differs"):
                conformance.verify_references(Path("fixture"), lock)
        self.assertEqual(("show", lock["revision"] + ":" + lock["references"][0]["path"]), read.call_args.args[1:])
        lock["revision"] = "HEAD"
        with self.assertRaisesRegex(ValueError, "revision"):
            conformance.verify_references(Path("fixture"), lock)

    def test_duplicate_missing_and_foreign_sources_are_rejected(self):
        raw = b"fixture source"
        row = {"repository": "cleanroom", "path": "fixture.java", "sha256": sha256(raw).hexdigest(),
               "gitBlob": sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
        lock = {"schema": "axiom.native-forge-registry-source-lock.v1", "revision": "a" * 40, "references": [row]}
        with patch.object(conformance, "git", return_value=raw):
            with self.assertRaisesRegex(ValueError, "closure"):
                conformance.verify_references(Path("fixture"), lock)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                conformance.verify_references(Path("fixture"), {**lock, "references": [row, row]})
            with self.assertRaisesRegex(ValueError, "unexpected"):
                conformance.verify_references(Path("fixture"), {**lock, "references": [{**row, "repository": "other"}]})

    def test_retained_edits_do_not_silently_reseal_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "Example.java"; path.write_text("original")
            with patch.object(conformance, "PATHS", {"Example": "source"}), patch.object(conformance, "extracted", return_value={"Example": "original"}):
                self.assertEqual({"Example": "original"}, conformance.retained_inputs({}, root))
                path.write_text("changed")
                with self.assertRaisesRegex(ValueError, "differs"):
                    conformance.retained_inputs({}, root)

    def test_source_edit_only_changes_max_only_factory_and_rejects_ambiguity(self):
        path = conformance.PATHS["ForgeEntryNames"]
        method = conformance.EDIT_METHOD + " { return builder.setMaxID(max); }"
        elsewhere = "void elsewhere() { builder.setMaxID(max); }"
        result = conformance.edited_source({path: method + elsewhere, "other": "unchanged"})
        self.assertEqual(method.replace("setMaxID(max)", "setIDRange(1, max)") + elsewhere, result[path])
        self.assertEqual("unchanged", result["other"])
        for source in (method + method, method.replace("setMaxID(max)", "setMaxID(2)")):
            with self.assertRaisesRegex(ValueError, "boundary"):
                conformance.edited_source({path: source})

    def test_native_naming_keeps_diagnostics_and_explicit_owner_port(self):
        source = (conformance.MATERIAL_ROOT / "ForgeEntryNames.java").read_text()
        self.assertIn("mc.injectedFmlContainer()", source)
        self.assertIn("FluidEnvironment.current().runtime().activeModContainer()", source)
        self.assertIn("toLowerCase(Locale.ROOT)", source)
        self.assertNotIn("GameData.init", source)
        self.assertNotIn("Enchantment.class", source)
        registry = (conformance.MATERIAL_ROOT / "ForgeRegistry.java").read_text()
        self.assertIn('"Invalid id %d - maximum id range exceeded."', registry)
        self.assertIn("mc.getModId().toLowerCase()", registry)
        self.assertNotIn("getRegisterEvent(", registry)
        self.assertNotIn("loadIds(", registry)

    def test_full_wrapper_and_entry_generics_do_not_manufacture_identities(self):
        wrapper = (conformance.MATERIAL_ROOT / "ForgeNamespacedRegistry.java").read_text()
        self.assertIn("extends NativeNamedRegistry<", wrapper)
        self.assertIn("super(FluidEnvironment.current().runtime())", wrapper)
        self.assertIn("this.delegate.add(id, value)", wrapper)
        entry = (conformance.MATERIAL_ROOT / "IForgeRegistryEntry.java").read_text()
        self.assertIn("new TypeToken<>(getClass())", entry)
        self.assertIn("delegate.name() != null", entry)
        self.assertNotIn("new Enchantment", wrapper + entry)
        defaulted = (conformance.MATERIAL_ROOT / "ForgeDefaultedRegistry.java").read_text()
        self.assertIn("extends NativeDefaultedRegistry<", defaulted)
        self.assertIn("super(FluidEnvironment.current().runtime(), null)", defaulted)
        self.assertIn("this.delegate.validateKey()", defaulted)
        self.assertIn("random.nextInt(values.size())", defaulted)

    def test_comparison_is_admitted_and_license_is_distributed(self):
        build = (ROOT / "modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../sources/native-forge-registries.lock.json")', build)
        self.assertIn('from("../tests/oracles/ForgeRegistryConformance.java")', build)
        self.assertIn("python3 tools/axiom_forge_registry_conformance.py", (ROOT / ".github/workflows/validate.yml").read_text())
        self.assertIn("native-forge-registries.lock.json", (ROOT / "modules/axiom/sources/NOTICE.md").read_text())
        self.assertTrue((ROOT / "modules/axiom/sources/licenses/forge/LGPL-2.1.txt").is_file())
        driver = conformance.DRIVER.read_text()
        self.assertIn("WorkerIsolation.install()", driver)
        self.assertIn('"wholePackParity", false', driver)
        self.assertIn("groovy.lang.GroovyShell", driver)
        with self.assertRaisesRegex(ValueError, "unadmitted"):
            extraction.extract("Unknown", "")
