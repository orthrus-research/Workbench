"""Installed distribution admission is separate from pip completion evidence."""

from __future__ import annotations

import base64
import csv
from hashlib import sha256
import io
import os
from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import environment_package_admission as admission
from workbench_core import environment_package_install as install
from workbench_core import environment_package_install_plan as preflight
from workbench_core.environment_reconstruction import ReconstructionError
from workbench_core.environment_retained_wheel import opened_retained_wheel
from workbench_core.storage.registered import CoreDurableResources

import test_environment_package_install as install_fixture


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL installed package admission")
class EnvironmentPackageAdmissionTests(TestCase):
    def setUp(self) -> None:
        install_fixture.EnvironmentPackageInstallTests.setUp(self)
        self.preflight = install_fixture.EnvironmentPackageInstallTests._plan(self)
        with patch.object(install, "_install", install_fixture.EnvironmentPackageInstallTests._benign_processes):
            self.installed = install_fixture.EnvironmentPackageInstallTests._apply(self, self.preflight)
        self._materialize_wheels()
        commands = {stage: [sys.executable, "-c", f"print('{stage} captured')"]
                    for stage in ("install", "check")}
        synthetic_commands = patch.object(admission, "_commands", return_value=commands)
        synthetic_commands.start()
        self.addCleanup(synthetic_commands.stop)

    def _materialize_wheels(self) -> None:
        site = Path(self.preflight["installation_paths"]["purelib"])
        site.mkdir(parents=True, exist_ok=True)
        for wheel in self.closure["wheels"]:
            files: dict[str, bytes] = {}
            with ZipFile(Path(self.package["tree_path"]) / "wheels" / wheel["filename"]) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    files[member.filename] = archive.read(member)
            metadata_root = next(name.rsplit("/", 1)[0] for name in files
                                 if name.endswith(".dist-info/METADATA"))
            files[metadata_root + "/INSTALLER"] = b"pip\n"
            files[metadata_root + "/REQUESTED"] = b""
            for name, data in files.items():
                target = site / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            lines = []
            for name, data in sorted(files.items()):
                digest = base64.urlsafe_b64encode(sha256(data).digest()).decode().rstrip("=")
                lines.append((name, "sha256=" + digest, str(len(data))))
            lines.append((metadata_root + "/RECORD", "", ""))
            output = io.StringIO(newline="")
            csv.writer(output, lineterminator="\n").writerows(lines)
            (site / metadata_root / "RECORD").write_text(output.getvalue(), encoding="utf-8")

    def _admit(self) -> dict:
        return admission.admit_package_install(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            install_result_resource_id=self.installed["resource"]["resource_id"],
            environment=self.environment,
        )

    def _reopen(self, result: dict) -> dict:
        return admission.reopen_package_admission(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            install_result_resource_id=self.installed["resource"]["resource_id"],
            admission_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_admit_reopen_reuse_and_preserve_other_unresolved_inputs(self) -> None:
        result = self._admit()
        self.assertEqual("installed-packages-admitted", result["state"])
        self.assertEqual(["optional-module-packages"], result["resolved_inputs"])
        self.assertNotIn("optional-module-packages", result["remaining_unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", result["remaining_unresolved_inputs"])
        self.assertIn("optional-module-packages", self.installed["unresolved_inputs"])
        self.assertEqual(len(self.closure["wheels"]), len(result["distributions"]))
        self.assertEqual(result["admission_id"], self._reopen(result)["admission_id"])
        self.assertEqual(result["resource"]["resource_id"], self._admit()["resource"]["resource_id"])

    def test_changed_member_or_extra_file_refuses_admission_and_reopen(self) -> None:
        result = self._admit()
        site = Path(self.preflight["installation_paths"]["purelib"])
        metadata = site / "helper-1.0.dist-info/METADATA"
        metadata.write_bytes(metadata.read_bytes() + b"changed")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)
        with self.assertRaises(ReconstructionError):
            self._admit()
        metadata.write_bytes(metadata.read_bytes().removesuffix(b"changed"))
        (site / "unowned.py").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "missing or extra files"):
            self._reopen(result)

    def test_record_hash_and_missing_wheel_member_refuse(self) -> None:
        site = Path(self.preflight["installation_paths"]["purelib"])
        record = site / "helper-1.0.dist-info/RECORD"
        raw = record.read_text(encoding="utf-8")
        record.write_text(raw.replace("sha256=", "sha256=changed", 1), encoding="utf-8")
        with self.assertRaises(ReconstructionError):
            self._admit()
        record.write_text(raw, encoding="utf-8")
        (site / "helper-1.0.dist-info/WHEEL").unlink()
        with self.assertRaises(ReconstructionError):
            self._admit()

    def test_installed_site_redirect_is_refused(self) -> None:
        site = Path(self.preflight["installation_paths"]["purelib"])
        (site / "redirect.py").symlink_to(site / "helper-1.0.dist-info/METADATA")
        with self.assertRaisesRegex(ReconstructionError, "cannot be inventoried"):
            self._admit()

    def test_unexpected_empty_directory_is_refused(self) -> None:
        site = Path(self.preflight["installation_paths"]["purelib"])
        (site / "unowned-empty").mkdir()
        with self.assertRaisesRegex(ReconstructionError, "missing or extra directories"):
            self._admit()

    def test_published_result_interruption_reuses_exact_resource(self) -> None:
        original = CoreDurableResources.publish_bytes

        def interrupt(service, role, name, data, **kwargs):
            result = original(service, role, name, data, **kwargs)
            if name == "environment-package-admission.json":
                raise RuntimeError("admission result acknowledgement interrupted")
            return result

        with patch.object(CoreDurableResources, "publish_bytes", interrupt):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement interrupted"):
                self._admit()
        result = self._admit()
        self.assertEqual(result["resource"]["resource_id"], self._admit()["resource"]["resource_id"])

    def test_changed_retained_wheel_and_wsl_mount_refuse(self) -> None:
        wheel = Path(self.package["tree_path"]) / "wheels/helper-1.0-py3-none-any.whl"
        wheel.write_bytes(wheel.read_bytes() + b"changed")
        with self.assertRaises(ReconstructionError):
            self._admit()
        wheel.write_bytes(wheel.read_bytes().removesuffix(b"changed"))
        with patch.object(preflight, "_mount_type", return_value="9p"):
            with self.assertRaises(ReconstructionError):
                self._admit()

    def test_same_byte_wheel_replacement_during_comparison_refuses(self) -> None:
        wheel = Path(self.package["tree_path"]) / "wheels/helper-1.0-py3-none-any.whl"
        original = admission._source_digest
        replaced = False

        def replace_during_read(*args):
            nonlocal replaced
            if not replaced:
                replacement = wheel.with_suffix(".replacement")
                replacement.write_bytes(wheel.read_bytes())
                os.replace(replacement, wheel)
                replaced = True
            return original(*args)

        with patch.object(admission, "_source_digest", replace_during_read):
            with self.assertRaisesRegex(ReconstructionError, "source path changed during ZIP inspection"):
                self._admit()

    def test_wheel_parent_redirect_during_zip_use_refuses(self) -> None:
        wheel = Path(self.package["tree_path"]) / "wheels/helper-1.0-py3-none-any.whl"
        row = next(row for row in self.closure["wheels"] if row["filename"] == wheel.name)
        parked = wheel.parent.with_name("wheels-parked")
        with self.assertRaisesRegex(ReconstructionError, "source path changed during ZIP inspection"):
            with opened_retained_wheel(wheel, row) as archive:
                self.assertTrue(archive.namelist())
                wheel.parent.rename(parked)
                wheel.parent.mkdir(mode=0o700)

    def test_unmatched_or_missing_command_identity_refuses(self) -> None:
        with patch.object(admission, "_commands", install._commands):
            with self.assertRaisesRegex(ReconstructionError, "command identity"):
                self._admit()
        previous = admission.reopen_package_install

        def legacy(*args, **kwargs):
            value = previous(*args, **kwargs)
            return {**value, "captures": {
                stage: {key: item for key, item in capture.items() if key != "argv_sha256"}
                for stage, capture in value["captures"].items()
            }}

        with patch.object(admission, "reopen_package_install", legacy):
            with self.assertRaisesRegex(ReconstructionError, "command identity"):
                self._admit()


if __name__ == "__main__":
    import unittest
    unittest.main()
