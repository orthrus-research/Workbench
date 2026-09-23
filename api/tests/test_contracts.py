from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from workbench_api.runtime import RuntimeProviderError, runtime_provider


class ContractTests(unittest.TestCase):
    def test_native_module_versions_match_release_authority(self):
        for value in ("0.1.0", "1.2.3a1", "1.2.3b2", "1.2.3rc1"):
            self.assertEqual(Module("sample", value).version, value)
        for value in ("1.2.3-alpha", "1.2.3+local", "1.2.3rc0", "01.2.3", "1.2.3post1"):
            with self.assertRaises(ModuleError):
                Module("sample", value)

    def test_installed_resources_do_not_fall_back_to_enclosing_checkout(self):
        from workbench_api.resources import repository_root
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "modules").mkdir()
            (root / "workbench.toml").touch()
            installed = root / ".workbench/venv/site-packages/module/code.py"
            with patch("workbench_api.resources.metadata.distributions", return_value=()):
                with self.assertRaises(ModuleError): repository_root(str(installed))

    def test_rejects_incompatible_api(self):
        for version in (0, 2, True, "1"):
            with self.assertRaises(ModuleError): Module("sample","0.1.0",api_version=version)

    def test_rejects_invalid_ids(self):
        for value in ("", "../outside", "UPPER", "has space"):
            with self.assertRaises(ModuleError): Module(value,"0.1.0")

    def test_rejects_duplicate_declarations(self):
        capability = Capability("sample.run",("sample",),"sample:run","Run")
        with self.assertRaises(ModuleError): Module("sample","0.1.0",(capability,capability))

    def test_rejects_unsafe_handler(self):
        with self.assertRaises(ModuleError): Capability("sample.run",("sample",),"../sample:run","Run")

    def test_cancellation_is_per_operation(self):
        first = ExecutionContext(Path.cwd(),Path.cwd())
        second = ExecutionContext(Path.cwd(),Path.cwd())
        first.cancelled.set()
        second.check_cancelled()
        with self.assertRaises(ModuleError): first.check_cancelled()

    def test_profile_does_not_have_implicit_fallback(self):
        with patch("workbench_api.runtime.metadata.entry_points",return_value=()):
            with self.assertRaises(RuntimeProviderError): runtime_provider("missing")


if __name__ == "__main__":
    unittest.main()
