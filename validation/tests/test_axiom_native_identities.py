"""Cleanroom constructor-input custody, acquisition and bounded consumer admission."""
from copy import deepcopy
from hashlib import sha1, sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_native_identity_conformance as native


class NativeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads(native.LOCK.read_bytes())
        self.policy = json.loads(native.POLICY.read_bytes())

    def test_exact_source_and_profile_closure(self):
        native.verify_policy(self.policy, self.lock, json.loads(native.PLATFORM.read_bytes()))
        expected = {("cleanroom", p) for p in native.COMPILED + native.AUDITED} | {("gtceu", p) for p in native.PRODUCERS}
        self.assertEqual(expected, {(r["repository"], r["path"]) for r in self.lock["references"]})
        self.assertEqual(15, len(self.lock["references"]))
        target = json.loads(native.TARGET_LOCK.read_bytes())
        self.assertEqual(next(r["commit"] for r in target["repositories"] if r["id"] == "gtceu"), self.lock["revisions"]["gtceu"])

    def test_identity_inputs_cannot_silently_choose_another_loader(self):
        platform = json.loads(native.PLATFORM.read_bytes())
        self.policy["inputs"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "platform artifact"): native.verify_policy(self.policy, self.lock, platform)
        self.policy["bootstrapSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "platform selection"): native.verify_policy(self.policy, self.lock, platform)

    def test_bad_policy_cannot_inflate_capability_or_change_paths(self):
        for mutate in (
                lambda p: p["images"].reverse(),
                lambda p: p["libraries"].pop(),
                lambda p: p["inputs"].append(p["inputs"][0]),
                lambda p: p["libraries"][0].update(path="../outside.jar"),
                lambda p: p["images"][0].update(size=True),
                lambda p: p["inputs"][0].update(sha256="wrong"),
                lambda p: p["inputs"][0].update(url="file:///outside.jar"),
                lambda p: p["qualification"].update(wholePackParity=True),
                lambda p: p.update(cleanroomRevision="HEAD")):
            with self.subTest(mutate=mutate):
                policy = deepcopy(self.policy); mutate(policy)
                with self.assertRaises(ValueError): native.verify_policy(policy, self.lock)

    def test_sources_read_immutable_objects_and_reject_changes(self):
        roots = {k: Path(k) for k in self.lock["revisions"]}
        with patch.object(native, "git", return_value=b"changed") as read:
            with self.assertRaisesRegex(ValueError, "differs"): native.verify_sources(roots, self.lock)
        row = self.lock["references"][0]
        self.assertEqual((roots[row["repository"]], "show", self.lock["revisions"][row["repository"]] + ":" + row["path"]), read.call_args.args)
        self.lock["revisions"][row["repository"]] = "HEAD"
        with self.assertRaisesRegex(ValueError, "mutable"): native.verify_sources(roots, self.lock)

    def test_duplicate_and_missing_source_closure(self):
        raw = b"source"
        for row in self.lock["references"]:
            row.update(sha256=sha256(raw).hexdigest(), gitBlob=sha1(b"blob 6\0" + raw).hexdigest())
        roots = {k: Path(k) for k in self.lock["revisions"]}
        with patch.object(native, "git", return_value=raw):
            self.assertEqual(15, len(native.verify_sources(roots, self.lock)))
            self.lock["references"].pop()
            with self.assertRaisesRegex(ValueError, "closure"): native.verify_sources(roots, self.lock)
            self.lock["references"].append(self.lock["references"][0])
            with self.assertRaisesRegex(ValueError, "duplicate"): native.verify_sources(roots, self.lock)

    def fixture_producers(self, count=5, expression="ToolProperty.Builder.of(4.0F, 3.0F, 384, 2).enchantment(Enchantments.EFFICIENCY, 1).build()"):
        return {("gtceu", native.PRODUCERS[0]): ("\n.toolStats(" + expression + ")").encode() * count,
                ("gtceu", native.PRODUCERS[1]): b""}

    def test_complete_tool_arguments_preserve_bytes_and_location(self):
        rows = native.expressions(self.fixture_producers())["expressions"]
        self.assertEqual(5, len(rows)); self.assertEqual(2, rows[0]["line"])
        self.assertTrue(rows[0]["source"].endswith(".build()"))
        self.assertEqual(sha256(rows[0]["source"].encode()).hexdigest(), rows[0]["sha256"])
        self.assertNotEqual(rows, native.expressions(self.fixture_producers(expression=rows[0]["source"].replace("384", "385")))["expressions"])

    def test_expression_drift_is_not_a_general_recipe_parser(self):
        for original in (self.fixture_producers(count=4), self.fixture_producers(expression="Enchantments.EFFICIENCY"),
                         self.fixture_producers(expression="ToolProperty.Builder.of('unexpected')"),
                         self.fixture_producers(expression="ToolProperty.Builder.of(/*comment*/)")):
            with self.assertRaises(ValueError): native.expressions(original)
        original = self.fixture_producers(); original["gtceu", native.PRODUCERS[0]] = b".toolStats("
        with self.assertRaisesRegex(ValueError, "unterminated"): native.expressions(original)

    def test_indirect_paths_and_output_overwrite_reject_before_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); link = root / "link"; link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "indirect"): native.ordinary(link / "new")
            args = [v for name in ("cleanroom", "gtceu", "java-home", "engine-home", "library-root") for v in ("--" + name, str(root))]
            with self.assertRaisesRegex(ValueError, "must be new"): native.main(args + ["--output", str(root)])

    def acquisition(self, raw=b"fixture"):
        return {"inputs": [{"path": "a/input.jar", "size": len(raw), "sha256": sha256(raw).hexdigest(), "url": native.ORIGINS[0] + "fixture.jar"}], "libraries": []}

    def test_acquisition_is_explicit_pinned_and_does_not_overwrite(self):
        policy = self.acquisition()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(native.urllib.request, "urlopen", return_value=io.BytesIO(b"fixture")) as download:
                native.provision_libraries(root, policy)
                self.assertEqual(1, download.call_count)
                native.provision_libraries(root, policy)
                self.assertEqual(1, download.call_count)
                (root / "a/input.jar").write_bytes(b"changed")
                with self.assertRaises(ValueError): native.provision_libraries(root, policy)
                self.assertEqual(b"changed", (root / "a/input.jar").read_bytes())
                self.assertEqual(1, download.call_count)

    def test_failed_download_never_promotes_a_candidate(self):
        for raw in (b"too large fixture", b"changed", b"short"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(native.urllib.request, "urlopen", return_value=io.BytesIO(raw)):
                    with self.assertRaises(ValueError): native.provision_libraries(root, self.acquisition())
                self.assertFalse((root / "a/input.jar").exists())

    def test_binding_is_external_native_classspace_not_a_second_registry(self):
        source = (native.MATERIAL_ROOT / "NativeVanillaIdentities.java").read_text()
        self.assertIn("new NativeMaterialClassLoader", source)
        self.assertIn("ClassLoader.getPlatformClassLoader()", (native.MATERIAL_ROOT / "NativeMaterialClassLoader.java").read_text())
        self.assertIn("NativeRuntime.verifyFiles(images, imageRows)", source)
        self.assertIn('phase = "failed"', source)
        self.assertIn('Class.forName("net.minecraft.init.Enchantments", true, loader)', source)
        self.assertNotIn("registerFluid(", source)
        self.assertNotIn("new NativeFluid(", source)
        self.assertNotIn("FluidRegistryState", source)

    def test_build_and_ci_admit_code_and_policy_not_minecraft_binaries(self):
        build = (ROOT / "modules/axiom/jvm/build.gradle.kts").read_text()
        self.assertIn('from("../sources/native-identities.lock.json")', build)
        self.assertIn('from("../../../profiles/platforms/cleanroom/native-identity-runtime.json")', build)
        self.assertNotIn("cleanroom-classes.jar", build)
        self.assertIn("python3 tools/axiom_native_identity_conformance.py", (ROOT / ".github/workflows/validate.yml").read_text())
        self.assertIn("native-identities.lock.json", (ROOT / "modules/axiom/sources/NOTICE.md").read_text())
        self.assertIn("WorkerIsolation.install()", native.BUILD.read_text())
        self.assertIn("WorkerIsolation.install()", native.DRIVER.read_text())
