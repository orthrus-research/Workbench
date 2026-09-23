import unittest
from unittest.mock import patch
from importlib.metadata import PackageNotFoundError

from workbench_api import ModuleError
from workbench_api.profiles import profile_scope, require_optional_distribution


class OptionalIntegrationTests(unittest.TestCase):
    def test_disabled_host_provider_is_refused_before_metadata_or_import(self):
        with profile_scope(unavailable_distributions=["Workbench_Axiom"]):
            with patch("workbench_api.profiles.metadata.version") as version:
                with self.assertRaisesRegex(ModuleError, "disabled"):
                    require_optional_distribution("workbench-axiom>=0.1.0,<0.2.0")
                version.assert_not_called()

    def test_selected_installed_provider_has_compatible_version(self):
        with patch("workbench_api.profiles.metadata.version", return_value="0.1.0"):
            self.assertEqual(require_optional_distribution("workbench-axiom>=0.1.0,<0.2.0"),
                             {"distribution": "workbench-axiom", "version": "0.1.0"})
        with patch("workbench_api.profiles.metadata.version", return_value="0.2.0"):
            with self.assertRaisesRegex(ModuleError, "incompatible"):
                require_optional_distribution("workbench-axiom>=0.1.0,<0.2.0")
        with patch("workbench_api.profiles.metadata.version", side_effect=PackageNotFoundError):
            with self.assertRaisesRegex(ModuleError, "not installed"):
                require_optional_distribution("workbench-axiom>=0.1.0,<0.2.0")

    def test_host_policy_resets_after_scope(self):
        with patch("workbench_api.profiles.metadata.version", return_value="0.1.0"):
            with profile_scope(unavailable_distributions=["workbench-axiom"]):
                with self.assertRaises(ModuleError):
                    require_optional_distribution("workbench-axiom>=0.1.0")
            require_optional_distribution("workbench-axiom>=0.1.0")
