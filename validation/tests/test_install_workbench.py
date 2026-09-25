"""No-clobber, offline admission and failure truth for native installations."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import install_workbench as installer
from verify_wheelhouse import verify, WheelhouseError


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.wheelhouse = self.base / "wheelhouse"
        wheels = self.wheelhouse / "wheels"
        wheels.mkdir(parents=True)
        self.wheel = wheels / "workbench_core-0.1.3-py3-none-any.whl"
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("workbench_core/__init__.py", "# fixture wheel bytes\n")
        self.record = {"filename": self.wheel.name, "name": "workbench-core", "version": "0.1.3", "size": self.wheel.stat().st_size, "sha256": hashlib.sha256(self.wheel.read_bytes()).hexdigest()}
        pip = wheels / "pip-26.1.2-py3-none-any.whl"
        with zipfile.ZipFile(pip, "w") as archive:
            archive.writestr("pip/__init__.py", "# fixture pip bytes\n")
        self.pip = {"filename": pip.name, "name": "pip", "version": "26.1.2", "size": pip.stat().st_size, "sha256": hashlib.sha256(pip.read_bytes()).hexdigest()}
        self.manifest = {"format": "workbench-native-wheelhouse-v1", "source_sha256": "a" * 64, "selected_components": ["workbench-core"], "native_versions": {"workbench-core": "0.1.3"}, "target": {"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": sys.platform, "machine": platform.machine()}, "wheels": [self.pip, self.record], "qualified": False}
        self.save()

    def save(self):
        (self.wheelhouse / "wheelhouse.json").write_text(json.dumps(self.manifest))
        (self.wheelhouse / "requirements.lock").write_text("".join(f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n" for row in self.manifest["wheels"]))

    def include_tui(self):
        wheel = self.wheelhouse / "wheels/workbench_tui-0.0.1-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("workbench_tui/__init__.py", "# fixture wheel bytes\n")
        self.manifest["wheels"].append({"filename": wheel.name, "name": "workbench-tui", "version": "0.0.1", "size": wheel.stat().st_size, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()})
        self.manifest["native_versions"]["workbench-tui"] = "0.0.1"
        self.manifest["selected_components"].append("workbench-tui")
        self.save()

    def test_verifies_exact_bytes_and_rejects_extra_files(self):
        self.assertEqual(self.manifest, verify(self.wheelhouse))
        extra = self.wheel.parent / "extra.whl"
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(WheelhouseError, "extra"):
            verify(self.wheelhouse)
        extra.unlink()
        self.wheel.write_bytes(b"corrupt")
        with self.assertRaisesRegex(WheelhouseError, "differs"):
            verify(self.wheelhouse)

    def test_rejects_indirection_traversal_and_lock_injection(self):
        self.record["filename"] = "../escape.whl"
        self.save()
        with self.assertRaises(WheelhouseError):
            verify(self.wheelhouse)
        self.record["filename"] = self.wheel.name
        self.save()
        (self.wheelhouse / "requirements.lock").write_text("--index-url https://example.invalid\n")
        with self.assertRaisesRegex(WheelhouseError, "requirements"):
            verify(self.wheelhouse)
        self.save()
        self.wheel.unlink()
        self.wheel.symlink_to(ROOT / "LICENSE")
        with self.assertRaises(WheelhouseError):
            verify(self.wheelhouse)

    def test_existing_destination_and_target_mismatch_do_not_mutate(self):
        destination = self.base / "installed"
        destination.mkdir()
        keep = destination / "retained.json"
        keep.write_text("keep")
        with self.assertRaisesRegex(WheelhouseError, "new"):
            installer.install(self.wheelhouse, destination)
        self.assertEqual("keep", keep.read_text())
        self.manifest["target"]["machine"] = "wrong"
        self.save()
        missing = self.base / "new-env"
        with self.assertRaisesRegex(WheelhouseError, "target"):
            installer.install(self.wheelhouse, missing)
        self.assertFalse(missing.exists())

    def test_legacy_windows_path_budget_uses_wheel_members_and_unicode(self):
        with patch.object(installer, "_windows_long_paths_enabled", return_value=False):
            installer._check_windows_paths(self.wheelhouse, PureWindowsPath("D:/Workbench 資料"), self.manifest)
            with self.assertRaisesRegex(WheelhouseError, "shorter destination"):
                installer._check_windows_paths(self.wheelhouse, PureWindowsPath("D:/" + "資料" * 120), self.manifest)
        with patch.object(installer, "_windows_long_paths_enabled", return_value=True):
            installer._check_windows_paths(self.wheelhouse, PureWindowsPath("D:/" + "資料" * 120), self.manifest)

    def test_windows_path_budget_includes_generated_bytecode_and_utf16_units(self):
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("module.py", "pass\n")
        with patch.object(installer, "_windows_long_paths_enabled", return_value=False):
            # Source fits; its generated __pycache__ entry does not.
            with self.assertRaisesRegex(WheelhouseError, "path limit"):
                installer._check_windows_paths(self.wheelhouse, PureWindowsPath("D:/" + "x" * 210), self.manifest)
            # Supplementary characters consume two Windows UTF-16 units each.
            with self.assertRaisesRegex(WheelhouseError, "path limit"):
                installer._check_windows_paths(self.wheelhouse, PureWindowsPath("D:/" + "😀" * 110), self.manifest)

    def test_undeclared_native_wheel_is_rejected_before_destination_creation(self):
        extra = self.wheel.parent / "workbench_hidden-0.1.0-py3-none-any.whl"
        extra.write_bytes(b"undeclared native fixture")
        self.manifest["wheels"].append({"filename": extra.name, "name": "workbench-hidden", "version": "0.1.0", "size": extra.stat().st_size, "sha256": hashlib.sha256(extra.read_bytes()).hexdigest()})
        self.save()
        destination = self.base / "rejected-extra-native"
        with patch.object(installer.venv.EnvBuilder, "create") as create:
            with self.assertRaisesRegex(WheelhouseError, "undeclared Workbench"):
                installer.install(self.wheelhouse, destination)
        create.assert_not_called()
        self.assertFalse(destination.exists())

    def test_missing_bootstrap_is_rejected_before_destination_creation(self):
        (self.wheel.parent / self.pip["filename"]).unlink()
        self.manifest["wheels"].remove(self.pip)
        self.save()
        destination = self.base / "rejected-missing-pip"
        with patch.object(installer.venv.EnvBuilder, "create") as create:
            with self.assertRaisesRegex(WheelhouseError, "pip bootstrap"):
                installer.install(self.wheelhouse, destination)
        create.assert_not_called()
        self.assertFalse(destination.exists())

    def test_unsupported_bootstrap_is_rejected_before_destination_creation(self):
        original = self.wheel.parent / self.pip["filename"]
        self.pip.update(version="25.0.0", filename="pip-25.0.0-py3-none-any.whl")
        original.rename(self.wheel.parent / self.pip["filename"])
        self.save()
        destination = self.base / "rejected-unsupported-pip"
        with patch.object(installer.venv.EnvBuilder, "create") as create:
            with self.assertRaisesRegex(WheelhouseError, "supported hash-locked pip"):
                installer.install(self.wheelhouse, destination)
        create.assert_not_called()
        self.assertFalse(destination.exists())

    def test_failure_records_incomplete_environment_without_cleanup(self):
        destination = self.base / "failed"
        with patch.object(installer.venv.EnvBuilder, "create", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                installer.install(self.wheelhouse, destination)
        receipt = json.loads((destination / "workbench-install.json").read_text())
        self.assertEqual("failed", receipt["state"])

    def test_pip_is_offline_isolated_hash_enforced_and_uses_private_copy(self):
        destination = self.base / "installed"
        with patch.object(installer.venv.EnvBuilder, "create"), patch.object(installer.subprocess, "run") as run:
            installer.install(self.wheelhouse, destination)
        command = run.call_args_list[0].args[0]
        for required in ("-I", "--isolated", "--no-index", "--require-hashes", "--only-binary=:all:"):
            self.assertIn(required, command)
        self.assertNotIn(str(self.wheelhouse / "wheels"), command)
        self.assertEqual(os.devnull, run.call_args_list[0].kwargs["env"]["PIP_CONFIG_FILE"])
        self.assertEqual("installed", json.loads((destination / "workbench-install.json").read_text())["state"])

    def test_optional_tui_launcher_is_probed_and_reported_without_user_config_write(self):
        self.include_tui()
        destination = self.base / "installed-with-tui"
        config_home = self.base / "retained-user-config"
        tui = destination / ("Scripts/workbench-tui.exe" if os.name == "nt" else "bin/workbench-tui")

        def create(_destination):
            tui.parent.mkdir(parents=True)
            tui.write_text("fixture launcher")

        with patch.object(installer.venv.EnvBuilder, "create", side_effect=create), patch.object(installer.subprocess, "run") as run, patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(config_home)}):
            result = installer.install(self.wheelhouse, destination)
        self.assertEqual(str(tui), result["tui_executable"])
        self.assertEqual(str(tui), json.loads((destination / "workbench-install.json").read_text())["tui_executable"])
        self.assertTrue(any(call.args[0] == [str(tui), "--help"] for call in run.call_args_list))
        self.assertFalse(config_home.exists())

    def test_optional_tui_missing_launcher_fails_install(self):
        self.include_tui()
        destination = self.base / "missing-tui-launcher"
        with patch.object(installer.venv.EnvBuilder, "create"), patch.object(installer.subprocess, "run"):
            with self.assertRaisesRegex(WheelhouseError, "workbench-tui launcher"):
                installer.install(self.wheelhouse, destination)
        receipt = json.loads((destination / "workbench-install.json").read_text())
        self.assertEqual("failed", receipt["state"])
