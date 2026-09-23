"""Offline artifact capture never changes its sources or weakens the declared hash."""

from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_axiom_artifacts as artifacts
from axiom_artifact_smoke import javap_event_methods


class AxiomArtifactBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="axiom-artifact-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "mods").mkdir()
        self.source = self.root / "mods/test.jar"
        self.source.write_bytes(b"fixture bytes; capture does not claim JAR validity")
        self.row = {"path": "mods/test.pw.toml", "outputPath": "mods/test.jar", "selection": "included-declaration",
                    "downloadHash": {"algorithm": "sha1", "value": sha1(self.source.read_bytes()).hexdigest()}}
        self.result = {"schema": "axiom.result.v1", "operation": "target", "status": "accepted", "result": {
            "targetId": "target", "candidateId": "candidate", "composition": {"compositionId": "composition", "side": "client", "options": {},
                "declarationInventoryComplete": True, "artifacts": [self.row]}}}

    def test_capture_is_deterministic_and_never_clobbers(self):
        one, two = self.root / "one.zip", self.root / "two.zip"
        first = artifacts.capture(self.result, self.root, one)
        second = artifacts.capture(self.result, self.root, two)
        self.assertEqual(first["artifactBundleId"], second["artifactBundleId"])
        self.assertEqual(one.read_bytes(), two.read_bytes())
        with self.assertRaisesRegex(ValueError, "new"):
            artifacts.capture(self.result, self.root, one)
        with zipfile.ZipFile(one) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            record, = manifest["artifacts"]
            self.assertEqual(self.source.read_bytes(), archive.read("blobs/" + record["sha256"]))
            self.assertEqual({"manifest.json", "blobs/" + sha256(self.source.read_bytes()).hexdigest()}, set(archive.namelist()))
            self.assertNotIn(str(self.root), archive.read("manifest.json").decode())

    def test_changed_artifact_never_passes_even_in_partial_mode(self):
        self.source.write_bytes(b"different")
        for partial in (False, True):
            with self.assertRaisesRegex(ValueError, "differs"):
                artifacts.capture(self.result, self.root, self.root / "candidate.zip", allow_partial=partial)
        self.assertFalse((self.root / "candidate.zip").exists())

    def test_missing_inputs_require_explicit_partial_capture(self):
        missing = dict(self.row, path="mods/missing.pw.toml", outputPath="mods/missing.jar")
        self.result["result"]["composition"]["artifacts"].append(missing)
        with self.assertRaisesRegex(ValueError, "missing"):
            artifacts.capture(self.result, self.root, self.root / "missing.zip")
        result = artifacts.capture(self.result, self.root, self.root / "partial.zip", allow_partial=True)
        self.assertEqual(["mods/missing.pw.toml"], result["missing"])

    def test_symlink_and_traversal_inputs_are_rejected(self):
        indirect = self.root / "mods/link.jar"
        indirect.symlink_to(self.source)
        self.row["outputPath"] = "mods/link.jar"
        with self.assertRaisesRegex(ValueError, "indirect"):
            artifacts.capture(self.result, self.root, self.root / "candidate.zip")
        self.row["outputPath"] = "../outside.jar"
        with self.assertRaises(ValueError):
            artifacts.capture(self.result, self.root, self.root / "candidate.zip")

    def test_unselected_or_incomplete_plan_is_not_captured(self):
        self.result["result"]["composition"]["side"] = "unspecified"
        with self.assertRaisesRegex(ValueError, "explicit"):
            artifacts.capture(self.result, self.root, self.root / "candidate.zip")
        self.result["result"]["composition"]["side"] = "client"
        self.result["result"]["composition"]["declarationInventoryComplete"] = False
        with self.assertRaises(ValueError):
            artifacts.capture(self.result, self.root, self.root / "candidate.zip")

    def test_no_empty_partial_success_and_no_external_output_replacement(self):
        self.row["selection"] = "excluded-declaration"
        with self.assertRaisesRegex(ValueError, "no selected"):
            artifacts.capture(self.result, self.root, self.root / "empty.zip", allow_partial=True)
        output = self.root / "output.zip"
        output.symlink_to(self.source)
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "new"):
            artifacts.capture(self.result, self.root, output)
        self.assertEqual(original, self.source.read_bytes())


class AxiomArtifactJavapTests(unittest.TestCase):
    WITNESS = """{
  public static void registerItems(fixture.Register<fixture.Item>);
    descriptor: (Lfixture/Register;)V
    flags: (0x0009) ACC_PUBLIC, ACC_STATIC
    Code:
      stack=0, locals=1, args_size=1
    Signature: #17                         // (Lfixture/Register<Lfixture/Item;>;)V
    RuntimeVisibleAnnotations:
      0: #18(#19=e#20.#21,#22=Z#23)
        net.minecraftforge.fml.common.eventhandler.SubscribeEvent(
          priority=Lnet/minecraftforge/fml/common/eventhandler/EventPriority;.HIGH
          receiveCanceled=true
        )
    RuntimeInvisibleParameterAnnotations:
      parameter 0:
        0: #24()
          fixture.NotNull

  public void unrelated();
    descriptor: ()V
    flags: (0x0001) ACC_PUBLIC
    RuntimeVisibleAnnotations:
      0: #25()
        net.minecraftforge.fml.relauncher.SideOnly
}
"""

    def test_javap_witness_retains_exact_method_metadata(self):
        method, = javap_event_methods(self.WITNESS)
        self.assertEqual("registerItems", method["name"])
        self.assertEqual("(Lfixture/Register;)V", method["descriptor"])
        self.assertEqual("(Lfixture/Register<Lfixture/Item;>;)V", method["signature"])
        self.assertEqual(9, method["access"])
        annotation, = method["annotations"]
        self.assertEqual("HIGH", annotation["values"]["priority"]["value"])
        self.assertIs(True, annotation["values"]["receiveCanceled"])

    def test_javap_witness_does_not_synthesize_defaults(self):
        no_values = self.WITNESS.replace("(\n          priority=Lnet/minecraftforge/fml/common/eventhandler/EventPriority;.HIGH\n          receiveCanceled=true\n        )", "")
        method, = javap_event_methods(no_values)
        self.assertEqual({}, method["annotations"][0]["values"])

    def test_javap_unknown_value_is_not_a_passing_comparison(self):
        with self.assertRaisesRegex(AssertionError, "Unsupported javap"):
            javap_event_methods(self.WITNESS.replace("receiveCanceled=true", "receiveCanceled=UNKNOWN"))
