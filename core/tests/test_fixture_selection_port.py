"""Core chooses the user record and preserves location-only fixture semantics."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module
from workbench_api.fixture_selections import fixture_selections, fixture_selections_scope
from workbench_core.fixture_selection import FixtureSelectionError
from workbench_core.fixture_selection_port import CoreFixtureSelections
from workbench_core.modules import InstalledModule, dispatch


class CoreFixtureSelectionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fixture-port-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "selected-config"
        self.other_config = self.root / "ambient-config"
        self.workspace = self.root / "selected-workspace"
        self.runtime = self.root / "missing-runtime"
        self.java = self.root / "missing-java"

    def test_bound_config_location_and_operation_override(self):
        host = CoreFixtureSelections(configuration_home=self.config)
        with patch.dict("os.environ", {"WORKBENCH_CONFIG_HOME": str(self.other_config)}):
            with fixture_selections_scope(host):
                record = fixture_selections().register(
                    "fixture:pack", self.workspace, self.runtime, self.java,
                )
                selected = fixture_selections().resolve("fixture:pack", self.workspace)
                self.assertEqual(record["id"], selected["registry_id"])
                self.assertEqual(str(self.runtime), selected["runtime"])
                self.assertEqual("user-registry", selected["java_source"])
                override = fixture_selections().resolve(
                    "fixture:pack", self.workspace, java_home=self.root / "alternate-java",
                )
                self.assertEqual("override", override["java_source"])
        self.assertTrue((self.config / "recipe-fixtures-v1.json").is_file())
        self.assertFalse((self.other_config / "recipe-fixtures-v1.json").exists())
        # The registry records user intent; byte compatibility remains the
        # selected profile's preflight responsibility.
        self.assertFalse(self.runtime.exists())

    def test_tampered_registry_is_not_silently_replaced(self):
        host = CoreFixtureSelections(configuration_home=self.config)
        host.register("fixture:pack", self.workspace, self.runtime, self.java)
        registry = self.config / "recipe-fixtures-v1.json"
        registry.write_text(registry.read_text().replace("missing-java", "other-java"))
        with self.assertRaisesRegex(FixtureSelectionError, "identity changed"):
            host.resolve("fixture:pack", self.workspace)
        selected = host.resolve(
            "fixture:pack", self.workspace, runtime=self.runtime, java_home=self.java,
        )
        self.assertEqual("override", selected["runtime_source"])

    def test_configuration_home_must_be_absolute(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            CoreFixtureSelections(configuration_home=Path("relative"))

    def test_exact_override_needs_no_registry_or_setup_read(self):
        host = CoreFixtureSelections(configuration_home=self.config)
        with patch("workbench_core.fixture_selection_port.default_user_config_home", return_value=self.config), \
             patch("workbench_core.fixture_selection_port.default_user_record_path",
                   side_effect=AssertionError("configuration must not be read")):
            selected = host.resolve(
                "fixture:pack", self.workspace, runtime=self.runtime, java_home=self.java,
            )
        self.assertEqual("override", selected["runtime_source"])
        self.assertFalse(self.config.exists())

    def test_admitted_dispatch_binds_core_fixture_host(self):
        capability = Capability("fixture.inspect", ("fixture",), "fixture_test:run", "Fixture test")
        module = Module("fixture-test", "0.1.0", (capability,))
        installed = InstalledModule("fixture-test", "fixture-test", "0.1.0", "available", module=module)
        context = ExecutionContext(
            workspace=self.workspace, state_root=self.root / "state",
            configuration_home=self.config,
        )

        def run(argv, *, context):
            self.assertEqual([], argv)
            fixture_selections().register("fixture:pack", self.workspace, self.runtime, self.java)
            selection = fixture_selections().resolve("fixture:pack", self.workspace)
            self.assertEqual(str(self.java), selection["java_home"])
            return 0

        with patch("workbench_core.modules.import_module", return_value=SimpleNamespace(run=run)):
            self.assertEqual(0, dispatch(["fixture"], context, (installed,)))
        self.assertTrue((self.config / "recipe-fixtures-v1.json").is_file())


if __name__ == "__main__":
    unittest.main()
