"""Per-user fixture locations are selections, never native evidence."""

from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest

from workbench_core.fixture_selection import (
    FixtureSelectionError, load_fixture_registry, register_recipe_fixture,
    resolve_java_library_fixture, resolve_recipe_fixture,
)


class RecipeFixtureSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fixture-selection-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = self.root / "config" / "recipe-fixtures-v1.json"
        self.setup = self.root / "config" / "missing-setup.json"
        self.workspace = self.root / "checkout é"
        self.runtime = self.root / "server 一"
        self.java = self.root / "JDK 8"

    def resolve(self, **overrides):
        return resolve_recipe_fixture("supersymmetry", self.workspace,
                                      registry_path=self.registry, setup_path=self.setup,
                                      **overrides)

    def test_registration_is_scoped_and_overrides_are_operation_only(self):
        record = register_recipe_fixture("supersymmetry", self.workspace, self.runtime,
                                         self.java, path=self.registry)
        self.assertEqual(record, load_fixture_registry(self.registry))
        selected = self.resolve()
        self.assertEqual(str(self.runtime), selected["runtime"])
        self.assertEqual(str(self.java), selected["java_home"])
        self.assertEqual(record["id"], selected["registry_id"])
        override = self.resolve(runtime=self.root / "alternate server")
        self.assertEqual("override", override["runtime_source"])
        self.assertEqual(str(self.java), override["java_home"])
        self.assertEqual(record, load_fixture_registry(self.registry))
        with self.assertRaisesRegex(FixtureSelectionError, "No runtime and Java selected"):
            resolve_recipe_fixture("another-pack", self.workspace,
                                   registry_path=self.registry, setup_path=self.setup)

    def test_explicit_selection_recovers_from_corrupt_registry(self):
        self.registry.parent.mkdir()
        self.registry.write_text('{"invalid": true}\n')
        selected = self.resolve(runtime=self.runtime, java_home=self.java)
        self.assertEqual("override", selected["runtime_source"])
        self.assertIsNone(selected["registry_id"])
        with self.assertRaisesRegex(FixtureSelectionError, "unsupported fields"):
            self.resolve()

    def test_record_is_tamper_evident(self):
        register_recipe_fixture("supersymmetry", self.workspace, self.runtime,
                                self.java, path=self.registry)
        raw = self.registry.read_text()
        self.registry.write_text(raw.replace("JDK 8", "JDK 9"))
        with self.assertRaisesRegex(FixtureSelectionError, "identity changed"):
            load_fixture_registry(self.registry)

    def test_exact_library_override_needs_a_matching_profile_digest(self):
        executable = self.java / "bin" / ("java.exe" if os.name == "nt" else "java")
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"synthetic Java executable")
        executable.with_name("javac.exe" if os.name == "nt" else "javac").write_bytes(b"synthetic compiler")
        library = self.runtime / "other layout" / "library.jar"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"fixture library")
        digest = sha256(library.read_bytes()).hexdigest()
        selected = resolve_java_library_fixture("supersymmetry", digest,
                                               java_executable=executable, library=library)
        self.assertEqual((executable, library), selected)
        with self.assertRaisesRegex(FixtureSelectionError, "does not match"):
            resolve_java_library_fixture("supersymmetry", "0" * 64,
                                         java_executable=executable, library=library)


if __name__ == "__main__":
    unittest.main()
