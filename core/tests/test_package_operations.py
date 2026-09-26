"""Core package mutation receipts survive failed installs and broken modules."""

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_api import ModuleError
from workbench_core import module_cli
from workbench_core.package_operations import PackageOperationStore


class PackageOperationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.guard = self.base / "guard"
        self.guard.mkdir(mode=0o700)
        self.environment_id = "a" * 64
        self.store = PackageOperationStore(self.guard, self.environment_id)
        patches = (
            patch("workbench_core.package_guard.guard_root", return_value=self.guard),
            patch("workbench_core.package_guard.environment_id", return_value=self.environment_id),
            patch("workbench_core.module_cli.default_runtime_state_root", return_value=self.base / "state"),
            patch.object(module_cli.sys, "prefix", str(self.base / "venv")),
            patch.object(module_cli.sys, "base_prefix", str(self.base / "base")),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _remove(self, *, pip_result=0, pip_error=None):
        module = SimpleNamespace(
            id="sample", distribution="sample-package",
            module=SimpleNamespace(requires=()),
        )
        observed = []

        def pip(_command):
            row, = (
                row for row in self.store.list(kind="modules")
                if row["state"] == "external-running"
            )
            observed.append(row)
            if pip_error is not None:
                raise pip_error
            return pip_result

        with patch.object(module_cli, "discover", return_value=(module,)), \
             patch.object(module_cli, "reverse_dependency_errors", return_value=[]), \
             patch.object(module_cli, "_pip", side_effect=pip), \
             redirect_stdout(StringIO()):
            result = module_cli.main(["remove", "sample"], root=self.base)
        return result, observed

    def test_successful_remove_is_readable_without_optional_module_discovery(self):
        result, observed = self._remove()
        self.assertEqual(0, result)
        self.assertEqual("external-running", observed[0]["state"])
        self.assertTrue(observed[0]["external_started"])
        row, = self.store.list(kind="modules")
        self.assertEqual("completed", row["state"])
        self.assertEqual("sample-package", row["distribution"])
        self.assertEqual(0, row["result_code"])
        self.assertEqual([], self.store.list(kind="profiles"))
        with patch.object(module_cli, "discover", side_effect=AssertionError("optional module was loaded")), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(0, module_cli.main(["operations", "--json"], root=self.base))
        self.assertEqual(row["record_id"], json.loads(output.getvalue())[0]["record_id"])
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(0, module_cli.main(["operations", row["operation_id"]], root=self.base))
        self.assertEqual(row, json.loads(output.getvalue()))
        with self.assertRaisesRegex(ModuleError, "another component kind"):
            module_cli.main(["operations", row["operation_id"]], root=self.base, kind="profiles")

    def test_setting_receipt_binds_selected_state_and_verified_readback(self):
        module = SimpleNamespace(id="sample", state="available")
        with patch.object(module_cli, "discover", return_value=(module,)):
            self.assertEqual(0, module_cli.main(["disable", "sample"], root=self.base))
        first, = self.store.list(kind="modules")
        selected = module_cli.configuration_path(self.base / "state", "modules")
        self.assertEqual("workbench-component-setting-operation-v1", first["format"])
        self.assertTrue(first["record_id"].startswith("workbench-component-setting-operation:sha256:"))
        self.assertEqual("completed", first["state"])
        self.assertEqual("disable", first["action"])
        self.assertEqual("sample", first["target"])
        self.assertEqual(str(selected), first["settings_path"])
        self.assertEqual(sha256(selected.read_bytes()).hexdigest(), first["settings_sha256"])
        self.assertFalse(first["external_started"])
        with patch.object(module_cli, "discover", side_effect=AssertionError("optional module was loaded")), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(0, module_cli.main(["operations", first["operation_id"]], root=self.base))
        self.assertEqual(first, json.loads(output.getvalue()))

        with patch.object(module_cli, "discover", return_value=(module,)):
            self.assertEqual(0, module_cli.main(["enable", "sample"], root=self.base))
        enabled = next(row for row in self.store.list(kind="modules") if row["action"] == "enable")
        self.assertEqual("completed", enabled["state"])
        self.assertEqual(str(selected), enabled["settings_path"])
        self.assertEqual(sha256(selected.read_bytes()).hexdigest(), enabled["settings_sha256"])
        self.assertEqual((), module_cli.disabled_modules(self.base / "state"))

        other = self.base / "other-state"
        with patch.object(module_cli, "default_runtime_state_root", return_value=other), \
             patch.object(module_cli, "discover", return_value=(module,)):
            self.assertEqual(0, module_cli.main(["disable", "sample"], root=self.base))
        paths = {row["settings_path"] for row in self.store.list(kind="modules")}
        self.assertEqual({str(selected), str(module_cli.configuration_path(other, "modules"))}, paths)
        with self.assertRaisesRegex(ModuleError, "identity or state"):
            PackageOperationStore(self.guard, "b" * 64).inspect(first["operation_id"])

    def test_profile_setting_receipt_keeps_component_kind(self):
        profile = SimpleNamespace(id="pack", state="available")
        with patch.object(module_cli, "_profile_status", return_value=(profile,)):
            self.assertEqual(0, module_cli.main(["disable", "pack"], root=self.base, kind="profiles"))
        row, = self.store.list(kind="profiles")
        self.assertEqual("completed", row["state"])
        self.assertEqual("profiles", row["kind"])
        self.assertEqual(("pack",), module_cli.disabled_profiles(self.base / "state"))

    def test_setting_preflight_and_post_start_failure_have_distinct_receipts(self):
        with patch.object(module_cli, "discover", return_value=()):
            with self.assertRaisesRegex(ModuleError, "select exactly one"):
                module_cli.main(["disable", "missing"], root=self.base)
        rejected, = self.store.list(kind="modules")
        self.assertEqual("rejected", rejected["state"])
        self.assertFalse(module_cli.configuration_path(self.base / "state", "modules").exists())

        module = SimpleNamespace(id="sample", state="available")
        observed = []

        def interrupted(*_args, **_kwargs):
            observed.extend(row for row in self.store.list(kind="modules") if row["target"] == "sample")
            raise RuntimeError("fixture write interrupted")

        with patch.object(module_cli, "discover", return_value=(module,)), \
             patch.object(module_cli, "_set_disabled", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "fixture write interrupted"):
                module_cli.main(["disable", "sample"], root=self.base)
        self.assertEqual("setting-running", observed[0]["state"])
        interrupted = next(row for row in self.store.list(kind="modules") if row["target"] == "sample")
        self.assertEqual("incomplete", interrupted["state"])
        self.assertIsNone(interrupted["settings_sha256"])
        self.assertIn("fixture write interrupted", interrupted["error"])

    def test_preflight_refusal_is_distinct_from_post_pip_uncertainty(self):
        with patch.object(module_cli, "discover", return_value=()), \
             patch.object(module_cli, "_pip") as pip:
            with self.assertRaisesRegex(ModuleError, "select exactly one"):
                module_cli.main(["remove", "missing"], root=self.base)
            pip.assert_not_called()
        rejected, = self.store.list(kind="modules")
        self.assertEqual("rejected", rejected["state"])
        self.assertFalse(rejected["external_started"])
        self.assertIsNone(rejected["result_code"])

        result, observed = self._remove(pip_result=17)
        self.assertEqual(17, result)
        self.assertEqual("external-running", observed[0]["state"])
        incomplete = next(row for row in self.store.list(kind="modules") if row["target"] == "sample")
        self.assertEqual("incomplete", incomplete["state"])
        self.assertTrue(incomplete["external_started"])
        self.assertEqual(17, incomplete["result_code"])

    def test_exception_after_pip_start_keeps_incomplete_record(self):
        with self.assertRaisesRegex(RuntimeError, "fixture pip interrupted"):
            self._remove(pip_error=RuntimeError("fixture pip interrupted"))
        row, = self.store.list(kind="modules")
        self.assertEqual("incomplete", row["state"])
        self.assertIn("fixture pip interrupted", row["error"])

    def test_install_semantic_rejection_after_pip_retains_exact_wheel_digest(self):
        wheel = self.base / "sample-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("sample-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: sample\nVersion: 0.1.0\n")
            archive.writestr("sample-0.1.0.dist-info/entry_points.txt", "[workbench.modules]\nsample = sample:module\n")
        from hashlib import sha256
        expected_sha256 = sha256(wheel.read_bytes()).hexdigest()
        with patch.object(module_cli, "dependency_errors", return_value=[]), \
             patch.object(module_cli, "reverse_dependency_errors", return_value=[]), \
             patch.object(module_cli.metadata, "entry_points", return_value=[]), \
             patch.object(module_cli, "validate_wheel_ownership"), \
             patch.object(module_cli, "_pip", return_value=0), \
             patch.object(module_cli.metadata, "version", return_value="wrong"):
            with self.assertRaisesRegex(ModuleError, "installed version verification failed"):
                module_cli.main(["install", str(wheel)], root=self.base)
        row, = self.store.list(kind="modules")
        self.assertEqual("incomplete", row["state"])
        self.assertTrue(row["external_started"])
        self.assertEqual(expected_sha256, row["wheel_sha256"])
        self.assertEqual("sample", row["distribution"])

    def test_other_environment_and_changed_record_cannot_be_reopened(self):
        self._remove()
        row, = self.store.list(kind="modules")
        with self.assertRaisesRegex(ModuleError, "identity or state"):
            PackageOperationStore(self.guard, "b" * 64).inspect(row["operation_id"])
        path = self.guard / "operations" / f"{row['operation_id']}.json"
        changed = json.loads(path.read_text())
        changed["state"] = "incomplete"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ModuleError, "identity or state"):
            self.store.inspect(row["operation_id"])


if __name__ == "__main__":
    unittest.main()
