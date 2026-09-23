"""Platform capture delegates selection and final verification to Java, retaining byte custody only."""

from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_axiom_platform as platform
from axiom_platform_smoke import javap_defaults


class AxiomPlatformBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="axiom-platform-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy, self.bootstrap = self.root / "policy.json", self.root / "bootstrap.zip"
        self.policy.write_bytes(b'{"fixture":true}')
        self.bootstrap.write_bytes(b"fake bootstrap for custody tests only")
        self.source = self.root / "g/a/1/a-1.jar"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"original input bytes; Java tests cover binary interpretation")
        self.selection = {"side": "client", "os": "linux", "architecture": "x86_64", "javaMajor": 25}
        self.row = {"path": "g/a/1/a-1.jar", "sha1": sha1(self.source.read_bytes()).hexdigest(), "size": self.source.stat().st_size}
        self.calls = []

    def inspect(self, path):
        self.calls.append(path.name)
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("manifest.json")
            manifest = json.loads(raw)
        provided = {row["path"] for row in manifest["artifacts"]}
        return {"schema": "axiom.result.v1", "operation": "platform", "status": "accepted", "result": {
            "schema": "axiom.platform-inspection.v1", "policySha256": manifest["policySha256"], "bootstrapSha256": manifest["bootstrapSha256"],
            "platformId": "axiom-platform:sha256:" + sha256(raw).hexdigest(), "providedArtifactBytesVerified": True,
            "missingArtifacts": [] if self.row["path"] in provided else [self.row["path"]],
            "metadata": {"selectionComplete": True, "selection": self.selection, "libraries": [self.row]}}}

    def capture(self, name="candidate.zip", inspect=None, **kwargs):
        return platform.capture(self.policy, self.bootstrap, self.selection, self.root, self.root / name, inspect or self.inspect, **kwargs)

    def test_deterministic_verified_capture_preserves_originals_and_has_no_paths(self):
        original = self.source.read_bytes()
        one, two = self.capture("one.zip"), self.capture("two.zip")
        self.assertEqual(one["platformId"], two["platformId"])
        self.assertEqual((self.root / "one.zip").read_bytes(), (self.root / "two.zip").read_bytes())
        self.assertEqual(["metadata.zip", "candidate.zip"] * 2, self.calls)
        self.assertEqual(original, self.source.read_bytes())
        with zipfile.ZipFile(self.root / "one.zip") as archive:
            self.assertEqual({"manifest.json", "policy.json", "bootstrap.zip", "blobs/" + sha256(original).hexdigest()}, set(archive.namelist()))
            self.assertNotIn(str(self.root), archive.read("manifest.json").decode())
        with self.assertRaisesRegex(ValueError, "new"):
            self.capture("one.zip")

    def test_missing_library_requires_explicit_partial_and_remains_missing(self):
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.capture()
        self.assertFalse((self.root / "candidate.zip").exists())
        result = self.capture(allow_partial=True)
        self.assertEqual([self.row["path"]], result["missing"])
        self.assertEqual(0, result["artifacts"])

    def test_wrong_bytes_size_or_policy_hash_never_pass_partial_mode(self):
        for key, value in (("sha1", "0" * 40), ("size", 1), ("policySha256", "0" * 64)):
            with self.subTest(key=key):
                saved = dict(self.row)
                self.row[key] = value
                with self.assertRaisesRegex(ValueError, "differs"):
                    self.capture(allow_partial=True)
                self.row = saved
        self.assertFalse((self.root / "candidate.zip").exists())

    def test_symlinks_and_traversal_are_not_byte_inputs(self):
        self.row["path"] = "../outside.jar"
        with self.assertRaises(ValueError):
            self.capture()
        link = self.root / "link.jar"
        link.symlink_to(self.source)
        self.row["path"] = "link.jar"
        with self.assertRaisesRegex(ValueError, "indirect"):
            self.capture()

    def test_failed_final_java_verification_cannot_promote(self):
        def reject(path):
            result = self.inspect(path)
            if path.name == "candidate.zip":
                result["status"] = "request-error"
            return result
        with self.assertRaisesRegex(ValueError, "accepted"):
            self.capture(inspect=reject)
        self.assertFalse((self.root / "candidate.zip").exists())
        self.assertEqual([], list(self.root.glob(".axiom-platform-*")))

    def test_capture_rejects_inspection_selection_or_identity_mismatch(self):
        for field in ("policySha256", "bootstrapSha256", "platformId"):
            def stale(path):
                result = self.inspect(path)
                result["result"][field] = "stale"
                return result
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.capture(inspect=stale)
        self.assertFalse((self.root / "candidate.zip").exists())

    def test_output_created_during_verification_is_not_overwritten(self):
        def competing(path):
            result = self.inspect(path)
            if path.name == "candidate.zip":
                (self.root / "candidate.zip").write_bytes(b"other writer")
            return result
        with self.assertRaises(FileExistsError):
            self.capture(inspect=competing)
        self.assertEqual(b"other writer", (self.root / "candidate.zip").read_bytes())


class AxiomPlatformJavapTests(unittest.TestCase):
    def test_member_boundaries_and_typed_defaults_are_not_substring_matches(self):
        output = '''{
  public abstract boolean receiveCanceled();
    descriptor: ()Z
    flags: (0x0401) ACC_PUBLIC, ACC_ABSTRACT
    AnnotationDefault:
      default_value: Z#21
        false

  public abstract fixture.Side[] value();
    descriptor: ()[Lfixture/Side;
    flags: (0x0401) ACC_PUBLIC, ACC_ABSTRACT
    AnnotationDefault:
      default_value: [e#19.#20,e#19.#21]
        [Lfixture/Side;.CLIENT,Lfixture/Side;.SERVER]

  public abstract java.lang.String required();
    descriptor: ()Ljava/lang/String;
    flags: (0x0401) ACC_PUBLIC, ACC_ABSTRACT
}
'''
        values = javap_defaults(output)
        self.assertEqual(["receiveCanceled", "value", "required"], [row["name"] for row in values])
        self.assertIs(False, values[0]["default"])
        self.assertEqual([{"enumType": "Lfixture/Side;", "value": "CLIENT"}, {"enumType": "Lfixture/Side;", "value": "SERVER"}], values[1]["default"])
        self.assertFalse(values[2]["defaultPresent"])
        self.assertNotIn("default", values[2])
        with self.assertRaises(AssertionError):
            javap_defaults(output.replace("        false", "        unrecognized value"))
