"""Focused observer Java contracts; synthetic receivers, no game initialization.

Uses the same Core profile fixture selection as the publisher tests, with
explicit environment overrides. This compiles serializer/snapshot helpers, not the Forge entry point.
The separately retained observer build verifies its original Forge classpath.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest

from workbench_core import tool_process
from workbench_core.fixture_selection import (
    FixtureSelectionError, find_artifact_by_digest, resolve_java_library_fixture,
    resolve_recipe_fixture,
)
from workbench_core.runtime_java import probe_java
from workbench_profile_supersymmetry.recipe_capture_inputs import QUALIFIED_ARTIFACTS


# Pinned by ForgeTranslationDataTest.java; this optional contract is separate
# from the native observer's required runtime-artifact tuple.
SUSSYPATCHES_FIXTURE_SHA256 = "c6ac9b9a9f920d1c21559cf0e414308d1d2ea1b5ae9efc5788125297d54b9224"


class ForgeRecipeObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_skip_reason = None
        java = os.environ.get("WORKBENCH_TEST_JAVA")
        server = os.environ.get("WORKBENCH_FORGE_TEST_SERVER_JAR")
        try:
            cls.java, cls.server = resolve_java_library_fixture(
                "supersymmetry", QUALIFIED_ARTIFACTS["minecraft_sha256"],
                java_executable=java, library=server)
        except FixtureSelectionError as exc:
            if java and server:
                raise AssertionError(str(exc)) from exc
            cls.fixture_skip_reason = (
                f"Core recipe fixture selection unavailable: {exc}. "
                "Register it with 'workbench capture recipes fixtures set' or use explicit test overrides")
            return
        feature = int(probe_java(cls.java)["java_version"].split(".", 1)[0])
        # The original Forge Gson reflects into JDK collections. Its Java 8
        # runtime has no module boundary; a modern test JVM needs this narrow
        # opening to exercise the same original serializer behavior.
        cls.module_arguments = ["--add-opens=java.base/java.util=ALL-UNNAMED"] if feature >= 9 else []
        selected_sussypatches = os.environ.get("WORKBENCH_FORGE_TEST_SUSSYPATCHES_JAR")
        cls.sussypatches, cls.sussypatches_reason = None, None
        if selected_sussypatches:
            selected = Path(selected_sussypatches)
            if sha256(selected.read_bytes()).hexdigest() != SUSSYPATCHES_FIXTURE_SHA256:
                raise AssertionError("explicit SussyPatches fixture differs from the original artifact")
            cls.sussypatches = selected
        else:
            try:
                fixture = resolve_recipe_fixture("supersymmetry")
                cls.sussypatches = find_artifact_by_digest(
                    fixture["runtime"], SUSSYPATCHES_FIXTURE_SHA256)
            except FixtureSelectionError as exc:
                cls.sussypatches_reason = str(exc)
        cls.javac = cls.java.with_name("javac")
        cls.temporary = tempfile.TemporaryDirectory(prefix="forge-observer-contracts-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.home = Path(cls.temporary.name)
        cls.classes = cls.home / "classes"
        cls.classes.mkdir()
        probes = Path(__file__).resolve().parents[1] / "probes"
        observer = probes / "forge-recipe-observer"
        package = Path("dev/workbench/crucible/forgerecipes")
        shared = probes / "ultimate-runtime-graph-producer/src/main/java/dev/workbench/crucible/runtimegraph"
        sources = [observer / "src/main/java" / package / name for name in (
            "ForgeReflection.java", "ForgeRecipeSnapshot.java", "ForgeBlockStateValue.java",
            "ForgeOrdinaryItemMatching.java", "ForgeCapabilityPreparation.java", "ForgeNbtCheck.java")]
        sources += [observer / "src/test/java" / package / name for name in (
            "ForgeReflectionTest.java", "ForgeItemNamesTest.java", "ForgeAbsentClientTypeTest.java",
            "ForgeNbtCheckTest.java", "ForgeArtifactOriginTest.java", "ForgeTranslationDataTest.java", "ForgeBlockStateValueTest.java", "ForgeOrdinaryItemMatchingTest.java", "ForgeCapabilityPreparationTest.java")]
        sources += [shared / "CanonicalJson.java", shared / "Hashing.java"]
        cls.inputs = {path: sha256(path.read_bytes()).hexdigest()
                      for path in [*sources, cls.java, cls.javac, cls.server,
                                   *([cls.sussypatches] if cls.sussypatches else [])]}
        cls.binding = sha256(json.dumps({str(path): digest for path, digest in cls.inputs.items()},
                                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result = tool_process.capture(
            [str(cls.javac), "-encoding", "UTF-8", "-source", "8", "-target", "8",
             "-cp", str(cls.server), "-d", str(cls.classes), *(str(path) for path in sources)],
            directory=cls.home / "compile", binding=cls.binding, cwd=cls.home, stdin=b"",
            environment={}, cancelled=Event(), timeout_seconds=120, output_limit=None)
        if result.exit_code:
            raise AssertionError(result.stderr.path.read_text())
        cls.assert_inputs_current()

    @classmethod
    def assert_inputs_current(cls):
        if any(sha256(path.read_bytes()).hexdigest() != digest for path, digest in cls.inputs.items()):
            raise AssertionError("observer contract-test inputs changed during execution")

    def setUp(self):
        if self.fixture_skip_reason is not None:
            self.skipTest(self.fixture_skip_reason)

    def run_contract(self, main, argument=None):
        self.assert_inputs_current()
        root = self.home / self._testMethodName
        root.mkdir(mode=0o700)
        result = tool_process.capture(
            [str(self.java), *self.module_arguments, "-cp", os.pathsep.join(map(str, (self.classes, self.server))),
             "dev.workbench.crucible.forgerecipes." + main, str(argument or root)],
            directory=root / "process", binding=self.binding, cwd=root, stdin=b"",
            environment={}, cancelled=Event(), timeout_seconds=120, output_limit=None)
        self.assert_inputs_current()
        self.assertEqual(0, result.exit_code, result.stderr.path.read_text())
        self.assertIn("no native initialization performed", result.stdout.path.read_text())

    def test_typed_nbt_reflection_and_item_identity(self):
        self.run_contract("ForgeReflectionTest")

    def test_actual_query_alias_shape_and_input_custody(self):
        self.run_contract("ForgeItemNamesTest")

    def test_unrelated_absent_client_signature_preserves_virtual_dispatch(self):
        self.run_contract("ForgeAbsentClientTypeTest")

    def test_compiled_copy_and_typed_nbt_parity(self):
        self.run_contract("ForgeNbtCheckTest")

    def test_local_archive_origins_and_original_copied_dispatch(self):
        self.run_contract("ForgeArtifactOriginTest")

    def test_original_listed_block_state_contract(self):
        self.run_contract("ForgeBlockStateValueTest")

    def test_one_shot_original_capability_preparation(self):
        self.run_contract("ForgeCapabilityPreparationTest")

    def test_ordinary_item_matching_occurrence_evidence(self):
        self.run_contract("ForgeOrdinaryItemMatchingTest")

    def test_original_sussypatches_translation_record(self):
        if self.sussypatches is None:
            self.skipTest("original SussyPatches 1.9.2 fixture is unavailable: " +
                          str(self.sussypatches_reason) +
                          "; register a matching runtime or use the exact test override")
        self.run_contract("ForgeTranslationDataTest", self.sussypatches)


if __name__ == "__main__":
    unittest.main()
