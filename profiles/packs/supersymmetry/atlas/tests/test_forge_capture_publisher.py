"""Java publisher/Crucible interoperability; synthetic records, no game launch."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest

from workbench_core import tool_process
from workbench_core.fixture_selection import FixtureSelectionError, resolve_java_library_fixture
from workbench_crucible_runtime_snapshot.capture import read_runtime_capture
from workbench_profile_supersymmetry.recipe_capture_inputs import QUALIFIED_ARTIFACTS


HARNESS = r'''
import java.util.*;
import dev.workbench.crucible.forgerecipes.ForgeCapturePublisher;
public class PublisherHarness {
  static Map<String,List<Map<String,Object>>> sample(String mode) {
    Map<String,List<Map<String,Object>>> result = new LinkedHashMap<>();
    for (String category : Arrays.asList("gt-recipes", "gt-recipe-maps", "gt-meta-tile-entities", "gt-machine-recipe-maps", "gt-item-matching", "gt-item-names", "gt-ordinary-item-matching")) {
      Map<String,Object> row = new LinkedHashMap<>();
      row.put("record_type", "synthetic-publisher-test");
      row.put("text", "NBT\n\u2603");
      row.put("nullable", null);
      row.put("number", 1.0);
      if (mode.equals("unstable") && category.equals("gt-recipes")) row.put("changed", true);
      result.put(category, new ArrayList<>(Arrays.asList(row)));
    }
    if (mode.equals("missing")) result.remove("gt-item-matching");
    if (mode.equals("empty")) result.get("gt-recipes").clear();
    return result;
  }
  public static void main(String[] args) throws Exception {
    try {
      if (!ForgeCapturePublisher.isEnabled()) { System.out.println("INERT"); return; }
      Map<String,Object> metadata = new LinkedHashMap<>();
      metadata.put("checkpoint_id", args[0].equals("checkpoint") ? "preInit" : "post-start-end-tick");
      metadata.put("physical_side", "dedicated_server");
      metadata.put("server_started", true);
      metadata.put("actual_event", "ServerTickEvent.END");
      if (args[0].startsWith("preparation")) {
        Map<String,Object> preparation = new LinkedHashMap<>();
        preparation.put("policy", "forge-loli-original-capability-materialization-v1");
        preparation.put("state", "complete");
        preparation.put("changed_object_count", 1);
        metadata.put("observation_preparation_policy", preparation.get("policy"));
        if (!args[0].equals("preparation-missing")) {
          ForgeCapturePublisher.requirePreparation();
          if (args[0].equals("preparation-begin-repeat")) ForgeCapturePublisher.requirePreparation();
          ForgeCapturePublisher.retainPreparation(preparation);
        }
        if (args[0].equals("preparation-repeat")) ForgeCapturePublisher.retainPreparation(preparation);
        if (args[0].equals("preparation-checkpoint")) metadata.remove("observation_preparation_policy");
      }
      ForgeCapturePublisher.publish(sample("normal"), sample(args[0]), metadata);
      if (args[0].equals("repeat")) ForgeCapturePublisher.publish(sample("normal"), sample("normal"), metadata);
      System.out.println("PUBLISHED");
    } catch (Throwable problem) {
      ForgeCapturePublisher.failure(problem);
      System.out.println("REFUSED:" + problem.getMessage());
    }
  }
}
'''


class ForgeCapturePublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_skip_reason = None
        selected_java = os.environ.get("WORKBENCH_TEST_JAVA")
        selected_server = os.environ.get("WORKBENCH_FORGE_TEST_SERVER_JAR")
        try:
            cls.java, cls.server = resolve_java_library_fixture(
                "supersymmetry", QUALIFIED_ARTIFACTS["minecraft_sha256"],
                java_executable=selected_java, library=selected_server)
        except FixtureSelectionError as exc:
            if selected_java and selected_server:
                raise AssertionError(str(exc)) from exc
            cls.fixture_skip_reason = (
                f"Core recipe fixture selection unavailable: {exc}. "
                "Register it with 'workbench capture recipes fixtures set' or use explicit test overrides")
            return
        cls.temporary = tempfile.TemporaryDirectory(prefix="forge-publisher-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.home = Path(cls.temporary.name)
        cls.classes = cls.home / "classes"
        cls.classes.mkdir()
        harness = cls.home / "PublisherHarness.java"
        harness.write_text(HARNESS)
        probes = Path(__file__).resolve().parents[1] / "probes"
        shared = probes / "ultimate-runtime-graph-producer/src/main/java/dev/workbench/crucible/runtimegraph"
        publisher = probes / "forge-recipe-observer/src/main/java/dev/workbench/crucible/forgerecipes/ForgeCapturePublisher.java"
        result = tool_process.capture(
            [str(cls.java.with_name("javac")), "-encoding", "UTF-8", "-source", "8", "-target", "8",
             "-cp", str(cls.server), "-d", str(cls.classes), str(harness), str(publisher),
             str(shared / "CanonicalJson.java"), str(shared / "Hashing.java")],
            directory=cls.home / "compile", binding="synthetic-forge-publisher-test",
            cwd=cls.home, stdin=b"", environment={}, cancelled=Event(), timeout_seconds=120, output_limit=None)
        if result.exit_code:
            raise AssertionError(result.stderr.path.read_text())

    def setUp(self):
        if self.fixture_skip_reason is not None:
            self.skipTest(self.fixture_skip_reason)
        self.root = self.home / self._testMethodName
        self.root.mkdir(mode=0o700)
        self.inputs = self.root / "input.json"
        self.inputs.write_text(json.dumps(dict(capture_id="publisher-test", launch_id="test-launch", physical_side="dedicated_server",
                                              candidate_lock_sha256="a" * 64, adapter_profile_sha256="b" * 64)))

    def launch(self, mode="normal", **properties):
        values = dict(enabled="true", capture_id="publisher-test", launch_id="test-launch",
                      input_manifest_path=str(self.inputs), input_manifest_sha256=sha256(self.inputs.read_bytes()).hexdigest(),
                      candidate_lock_sha256="a" * 64, adapter_profile_sha256="b" * 64,
                      output=str(self.root / "capture"))
        values.update(properties)
        return tool_process.capture(
            [str(self.java), *(f"-Dworkbench.runtimeGraph.{key}={value}" for key, value in values.items()),
             "-cp", os.pathsep.join(map(str, (self.classes, self.server))), "PublisherHarness", mode],
            directory=self.root / "process", binding="synthetic-forge-publisher-test",
            cwd=self.root, stdin=b"", environment={}, cancelled=Event(), timeout_seconds=120, output_limit=None)

    def test_java_canonical_seals_admit_through_crucible_reader(self):
        result = self.launch()
        self.assertEqual(0, result.exit_code, result.stderr.path.read_text())
        self.assertIn("PUBLISHED", result.stdout.path.read_text())
        capture = read_runtime_capture(self.root / "capture", input_manifest=self.inputs,
                                       categories=("gt-recipes", "gt-item-matching"))
        self.assertEqual("post-start-end-tick", capture.categories["gt-recipes"]["checkpoint_id"])
        self.assertIsNone(capture.categories["gt-recipes"]["records"][0]["nullable"])
        self.assertEqual("NBT\n☃", capture.categories["gt-recipes"]["records"][0]["text"])

    def test_unstable_sample_retains_failure_without_completion(self):
        result = self.launch("unstable")
        self.assertIn("REFUSED:unstable", result.stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())
        staging = self.root / "capture.staging-publisher-test"
        self.assertTrue((staging / "failure.json").is_file())
        failure = json.loads((staging / "failure.json").read_bytes())
        self.assertIn("ForgeCapturePublisher.publish", failure["stack_trace"])
        self.assertIn("unstable repeated category sample", failure["stack_trace"])
        self.assertFalse((staging / ".capture-complete").exists())

    def test_missing_category_refused(self):
        self.assertIn("REFUSED:capture category", self.launch("missing").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_empty_recipe_capture_refused(self):
        self.assertIn("REFUSED:", self.launch("empty").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_wrong_checkpoint_refused(self):
        self.assertIn("REFUSED:capture did not reach", self.launch("checkpoint").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_input_binding_tamper_refused(self):
        result = self.launch(input_manifest_sha256="f" * 64)
        self.assertIn("REFUSED:input manifest bytes", result.stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_existing_output_preserved(self):
        (self.root / "capture").mkdir()
        original = self.root / "capture/original"
        original.write_bytes(b"retain")
        self.assertIn("REFUSED:capture output", self.launch().stdout.path.read_text())
        self.assertEqual(b"retain", original.read_bytes())

    def test_protocol_lock_mismatch_refused(self):
        result = self.launch(candidate_lock_sha256="c" * 64)
        self.assertIn("REFUSED:input manifest has a different candidate_lock_sha256", result.stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_disabled_observer_is_inert(self):
        self.assertIn("INERT", self.launch(enabled="false").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())
        self.assertFalse((self.root / "capture.staging-publisher-test").exists())

    def test_repeated_publication_does_not_rewrite_success(self):
        result = self.launch("repeat")
        self.assertIn("REFUSED:capture already published", result.stdout.path.read_text())
        read_runtime_capture(self.root / "capture", input_manifest=self.inputs, categories=("gt-recipes",))

    def declare_preparation(self):
        inputs = json.loads(self.inputs.read_bytes())
        inputs["observation_preparation"] = {
            "policy": "forge-loli-original-capability-materialization-v1",
            "phase": "before-two-effective-samples",
        }
        self.inputs.write_text(json.dumps(inputs))

    def test_preparation_payload_is_bound_and_tamper_refused_by_crucible(self):
        self.declare_preparation()
        result = self.launch("preparation")
        self.assertIn("PUBLISHED", result.stdout.path.read_text())
        capture = read_runtime_capture(self.root / "capture", input_manifest=self.inputs, categories=("gt-recipes",))
        payload = self.root / "capture/capability-preparation.json"
        data = json.loads(payload.read_bytes())
        self.assertEqual("publisher-test", data["capture_id"])
        self.assertEqual("workbench-forge-item-capability-preparation-v1", data["format"])
        descriptor = next(row for row in capture.manifest["payloads"] if row["file"] == payload.name)
        self.assertEqual(sha256(payload.read_bytes()).hexdigest(), descriptor["sha256"])
        payload.write_bytes(payload.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "payload digest/size mismatch"):
            read_runtime_capture(self.root / "capture", input_manifest=self.inputs, categories=("gt-recipes",))

    def test_undeclared_preparation_refused(self):
        self.assertIn("REFUSED:capture preparation was not declared", self.launch("preparation").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_repeated_preparation_start_refused_before_payload(self):
        self.declare_preparation()
        self.assertIn("REFUSED:capture preparation already started", self.launch("preparation-begin-repeat").stdout.path.read_text())
        self.assertFalse((self.root / "capture.staging-publisher-test/capability-preparation.json").exists())

    def test_declared_preparation_must_be_retained_before_publication(self):
        self.declare_preparation()
        self.assertIn("REFUSED:capture preparation", self.launch("preparation-missing").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_preparation_checkpoint_policy_must_match(self):
        self.declare_preparation()
        self.assertIn("REFUSED:capture preparation", self.launch("preparation-checkpoint").stdout.path.read_text())
        self.assertFalse((self.root / "capture").exists())

    def test_repeated_preparation_preserves_first_payload_without_seal(self):
        self.declare_preparation()
        self.assertIn("REFUSED:preparation already retained", self.launch("preparation-repeat").stdout.path.read_text())
        staging = self.root / "capture.staging-publisher-test"
        self.assertEqual(1, json.loads((staging / "capability-preparation.json").read_bytes())["preparation"]["changed_object_count"])
        self.assertFalse((staging / ".capture-complete").exists())


if __name__ == "__main__":
    unittest.main()
