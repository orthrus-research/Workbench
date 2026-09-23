from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api/src"))

from workbench_api.profile_extensions import ProfileExtension, ProfileExtensionError, profile_extensions, require_profile_extension
from workbench_api.profiles import ProfileStatus


class ProfileExtensionTests(unittest.TestCase):
    def test_required_contract_rejects_missing_duplicate_and_wrong_api(self):
        for rows in ((), (ProfileExtension("pack", "available", SimpleNamespace(PROFILE_API_VERSION=2)),),
                     (ProfileExtension("pack", "unavailable", reason="dependency failed"),),
                     (ProfileExtension("pack", "available"), ProfileExtension("pack", "available"))):
            with patch("workbench_api.profile_extensions.profile_extensions", return_value=rows):
                with self.assertRaises(ProfileExtensionError):
                    require_profile_extension("workbench.test", "pack")

    def test_loaded_native_code_and_sibling_changes_require_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.py"
            sibling = Path(directory) / "policy.py"
            path.write_text("PROFILE_API_VERSION = 1\n")
            sibling.write_text("VALUE = 1\n")
            module = ModuleType("profile_owner")
            module.__file__ = str(path)
            module.PROFILE_API_VERSION = 1
            rows = (ProfileExtension("pack", "available", module),)
            with patch("workbench_api.profile_extensions.profile_extensions", return_value=rows):
                self.assertIs(module, require_profile_extension("workbench.test", "pack"))
                sibling.write_text("VALUE = 2\n")
                with self.assertRaisesRegex(ProfileExtensionError, "restart"):
                    require_profile_extension("workbench.test", "pack")

    def test_packaged_non_python_observer_changes_require_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.py"
            observer = Path(directory) / "observer.groovy"
            path.write_text("PROFILE_API_VERSION = 1\n")
            observer.write_text("capture()\n")
            module = ModuleType("observer_owner")
            module.__file__ = str(path)
            module.PROFILE_API_VERSION = 1
            rows = (ProfileExtension("pack", "available", module),)
            with patch("workbench_api.profile_extensions.profile_extensions", return_value=rows):
                self.assertIs(module, require_profile_extension("workbench.test", "pack"))
                observer.write_text("differentCapture()\n")
                with self.assertRaisesRegex(ProfileExtensionError, "restart"):
                    require_profile_extension("workbench.test", "pack")

    def entry(self, name, owner, value=None, error=None):
        def load():
            if error:
                raise error
            return value

        return SimpleNamespace(name=name, dist=SimpleNamespace(metadata={"Name": owner}), load=load)

    def discover(self, entries, statuses):
        with patch("workbench_api.profile_extensions.metadata.entry_points", return_value=entries), patch("workbench_api.profile_extensions.profile_status", return_value=statuses):
            return profile_extensions("workbench.test_extensions")

    def test_only_admitted_profile_distribution_can_contribute(self):
        status = ProfileStatus("example", "workbench-profile-example", "1.0.0", "available")
        valid = self.entry("example", "Workbench_Profile_Example", value="owner")
        self.assertEqual(self.discover((valid,), (status,))[0].value, "owner")
        intruder = self.entry("example", "another-owner", value="intruder")
        self.assertEqual(self.discover((intruder,), (status,))[0].state, "unavailable")

    def test_disabled_missing_and_duplicated_owners_do_not_load(self):
        disabled = ProfileStatus("example", "workbench-profile-example", "1.0.0", "disabled")
        entry = self.entry("example", "workbench-profile-example", error=AssertionError("must not load"))
        for statuses in ((), (disabled,)):
            result, = self.discover((entry,), statuses)
            self.assertEqual(result.reason, "owning profile is not admitted and enabled")
        admitted = ProfileStatus("example", "workbench-profile-example", "1.0.0", "available")
        self.assertTrue(all(result.reason == "extension identity is duplicated" for result in self.discover((entry, entry), (admitted,))))

    def test_broken_optional_owner_does_not_disable_another_extension(self):
        statuses = tuple(ProfileStatus(name, name, "1.0.0", "available") for name in ("broken", "healthy"))
        entries = (self.entry("broken", "broken", error=ImportError("missing dependency")), self.entry("healthy", "healthy", value="ready"))
        broken, healthy = self.discover(entries, statuses)
        self.assertEqual(broken.state, "unavailable")
        self.assertEqual(healthy.value, "ready")
