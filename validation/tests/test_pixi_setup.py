"""Pixi setup binds repository inputs and reports only observable guarantees."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import pixi_setup  # noqa: E402


class PixiSetupTests(unittest.TestCase):
    PIXI_TOOL = {
        "path": "/tools/pixi",
        "version": "0.75.0",
        "sha256": "f" * 64,
        "size": 123,
        "required_constraint": "==0.75.0",
        "identity_scope": "post-launch-custody-copy",
        "launch_time_identity_claimed": False,
    }

    @staticmethod
    def _environment(name: str = "default") -> dict[str, str]:
        return {
            "PIXI_ENVIRONMENT_NAME": name,
            "PIXI_PROJECT_MANIFEST": str(ROOT / "pixi.toml"),
        }

    @staticmethod
    def _runtime(
        *,
        environment: str = "default",
        python: str = "3.14.6",
        system: str = "Linux",
        machine: str = "x86_64",
    ) -> dict[str, str]:
        prefix = ROOT / ".pixi" / "envs" / environment
        executable_name = "python.exe" if system == "Windows" else "bin/python"
        return {
            "python": python,
            "implementation": "CPython",
            "system": system,
            "machine": machine,
            "prefix": str(prefix),
            "executable": str(prefix / executable_name),
        }

    def test_attention_binds_inputs_without_claiming_invocation_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            responses = [
                {"product": "Workbench"},
                {"summary": {"status": "attention"}},
            ]
            with (
                patch.dict(os.environ, self._environment(), clear=False),
                patch.object(
                    pixi_setup, "_runtime_facts", return_value=self._runtime()
                ),
                patch.object(
                    pixi_setup,
                    "_verified_pixi_tool",
                    return_value=self.PIXI_TOOL,
                ),
                patch.object(pixi_setup, "_json_command", side_effect=responses),
            ):
                result = pixi_setup.build_setup_result(
                    workspace,
                    provision_java=False,
                )

            self.assertEqual(
                (result["format"], result["schema_version"]),
                ("workbench-pixi-setup-result-v2", 2),
            )
            self.assertEqual("attention", result["outcome"])
            self.assertEqual(result["environment"]["name"], "default")
            pixi = result["pixi"]
            self.assertEqual(pixi["requirements"]["pixi"], "==0.75.0")
            self.assertEqual(pixi["requirements"]["python"], "==3.14.6")
            self.assertEqual(pixi["runtime"]["platform_name"], "linux-x86-64")
            self.assertEqual(pixi["runtime"]["platform_subdir"], "linux-64")
            self.assertEqual(pixi["tool"], self.PIXI_TOOL)
            self.assertTrue(pixi["verification"]["pixi_executable_version"])
            self.assertFalse(
                pixi["verification"]["pixi_launch_time_executable_identity"]
            )
            self.assertFalse(pixi["tool"]["launch_time_identity_claimed"])
            self.assertEqual(
                pixi["project"]["manifest"]["sha256"],
                hashlib.sha256((ROOT / "pixi.toml").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                pixi["project"]["lock"]["sha256"],
                hashlib.sha256((ROOT / "pixi.lock").read_bytes()).hexdigest(),
            )
            self.assertTrue(
                pixi["verification"]["manifest_and_lock_identities_bound"]
            )
            self.assertTrue(
                pixi["verification"]["expected_closure_derived_from_bound_inputs"]
            )
            self.assertRegex(
                pixi["expected_closure"]["closure_id"],
                r"^workbench-pixi-environment-closure-v1:sha256:[0-9a-f]{64}$",
            )
            self.assertFalse(
                pixi["verification"]["complete_environment_closure"]
            )
            for flag in ("locked", "no_config"):
                self.assertEqual(
                    pixi["invocation"][flag],
                    {
                        "status": "not-observable",
                        "claimed": False,
                        "reason": (
                            "Pixi does not expose the launch flag to this task process"
                        ),
                    },
                )
            human = pixi_setup._human(result)
            self.assertIn("needs attention", human)
            self.assertIn("--locked and --no-config are not observable", human)
            self.assertNotIn("ready in the locked Pixi environment", human)

    def test_native_release_derives_manifest_python_and_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            responses = [
                {"product": "Workbench"},
                {"summary": {"status": "ready"}},
            ]
            with (
                patch.dict(
                    os.environ, self._environment("compatibility"), clear=False
                ),
                patch.object(
                    pixi_setup,
                    "_runtime_facts",
                    return_value=self._runtime(
                        environment="compatibility",
                        python="3.13.14",
                        system="Darwin",
                        machine="arm64",
                    ),
                ),
                patch.object(
                    pixi_setup,
                    "_verified_pixi_tool",
                    return_value=self.PIXI_TOOL,
                ),
                patch.object(pixi_setup, "_json_command", side_effect=responses),
            ):
                result = pixi_setup.build_setup_result(
                    Path(temporary), provision_java=False
                )

        pixi = result["pixi"]
        self.assertEqual(pixi["requirements"]["python"], "==3.13.14")
        self.assertEqual(pixi["runtime"]["platform_name"], "macos-arm64")
        self.assertEqual(pixi["runtime"]["platform_subdir"], "osx-arm64")
        self.assertFalse(pixi["environment"]["uses_default_feature"])
        self.assertEqual(pixi["environment"]["features"], ["compatibility-runtime"])
        self.assertIn(
            "/osx-arm64/python-3.13.14-",
            pixi["lock_environment"]["python_package"],
        )

    def test_manifest_must_be_this_repository_exact_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            other_manifest = Path(temporary) / "pixi.toml"
            other_manifest.write_bytes((ROOT / "pixi.toml").read_bytes())
            environment = self._environment()
            environment["PIXI_PROJECT_MANIFEST"] = str(other_manifest)
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "does not identify this repository's exact pixi.toml",
                ):
                    pixi_setup.build_setup_result(
                        Path(temporary), provision_java=False
                    )

    def test_setup_derives_closure_and_hashes_from_one_bound_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            manifest_path = repository / "pixi.toml"
            lock_path = repository / "pixi.lock"
            manifest_raw = (ROOT / "pixi.toml").read_bytes()
            lock_raw = (ROOT / "pixi.lock").read_bytes()
            manifest_path.write_bytes(manifest_raw)
            lock_path.write_bytes(lock_raw)
            prefix = repository / ".pixi/envs/default"
            runtime = self._runtime(python="3.14.6")
            runtime["prefix"] = str(prefix)
            runtime["executable"] = str(prefix / "bin/python")
            real_bind = pixi_setup.bind_pixi_inputs

            def bind_then_mutate(manifest: Path, lock: Path):
                inputs = real_bind(manifest, lock)
                manifest.write_bytes(inputs.manifest_bytes + b"\n")
                lock.write_bytes(inputs.lock_bytes + b"\n")
                return inputs

            with (
                patch.object(pixi_setup, "ROOT", repository),
                patch.object(
                    pixi_setup,
                    "bind_pixi_inputs",
                    side_effect=bind_then_mutate,
                ),
                patch.object(
                    pixi_setup,
                    "_runtime_facts",
                    return_value=runtime,
                ),
                patch.object(
                    pixi_setup,
                    "_verified_pixi_tool",
                    return_value=self.PIXI_TOOL,
                ),
            ):
                result = pixi_setup._verified_pixi_environment(
                    "default",
                    str(manifest_path),
                )

            self.assertEqual(
                hashlib.sha256(manifest_raw).hexdigest(),
                result["project"]["manifest"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(lock_raw).hexdigest(),
                result["project"]["lock"]["sha256"],
            )
            self.assertTrue(
                result["verification"][
                    "expected_closure_derived_from_bound_inputs"
                ]
            )
            self.assertNotEqual(
                result["project"]["lock"]["sha256"],
                hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            )

    def test_setup_rejects_symlinked_lock_and_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(
            pixi_setup.PixiSetupError,
            "duplicate mapping key",
        ):
            pixi_setup._parse_lock(b"version: 7\nversion: 8\n")

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            manifest_path = repository / "pixi.toml"
            manifest_path.write_bytes((ROOT / "pixi.toml").read_bytes())
            (repository / "pixi.lock").symlink_to(ROOT / "pixi.lock")
            with (
                patch.object(pixi_setup, "ROOT", repository),
                self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "not one bounded ordinary file",
                ),
            ):
                pixi_setup._verified_pixi_environment(
                    "default",
                    str(manifest_path),
                )

    def test_project_local_pixi_config_is_rejected_before_setup_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            config = project_root / ".pixi/config.toml"
            config.parent.mkdir()
            config.write_text("[mirrors]\n", encoding="utf-8")
            with (
                patch.dict(os.environ, self._environment(), clear=False),
                patch.object(pixi_setup, "ROOT", project_root),
            ):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "project-local configuration.*regular file",
                ):
                    pixi_setup.build_setup_result(
                        project_root, provision_java=False
                    )

    def test_undeclared_environment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                os.environ, self._environment("invented"), clear=False
            ):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "environment is not declared",
                ):
                    pixi_setup.build_setup_result(
                        Path(temporary), provision_java=False
                    )

    def test_python_must_match_manifest_derived_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict(os.environ, self._environment(), clear=False),
                patch.object(
                    pixi_setup,
                    "_runtime_facts",
                    return_value=self._runtime(python="3.14.5"),
                ),
            ):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "requires Python 3.14.6.*observed 3.14.5",
                ):
                    pixi_setup.build_setup_result(
                        Path(temporary), provision_java=False
                    )

    def test_python_must_run_from_repository_environment_prefix(self) -> None:
        runtime = self._runtime()
        runtime["prefix"] = "/tmp/not-workbench-pixi"
        runtime["executable"] = "/tmp/not-workbench-pixi/bin/python"
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict(os.environ, self._environment(), clear=False),
                patch.object(
                    pixi_setup, "_runtime_facts", return_value=runtime
                ),
            ):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "Python is not running from this repository's 'default' "
                    "Pixi prefix",
                ):
                    pixi_setup.build_setup_result(
                        Path(temporary), provision_java=False
                    )

    def test_environment_platform_restriction_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict(
                    os.environ, self._environment("workspace"), clear=False
                ),
                patch.object(
                    pixi_setup,
                    "_runtime_facts",
                    return_value=self._runtime(
                        environment="workspace",
                        system="Windows",
                        machine="ARM64",
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "environment 'workspace' is not declared for windows-arm64",
                ):
                    pixi_setup.build_setup_result(
                        Path(temporary), provision_java=False
                    )

    def test_unknown_doctor_status_fails_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict(os.environ, self._environment(), clear=False),
                patch.object(
                    pixi_setup, "_runtime_facts", return_value=self._runtime()
                ),
                patch.object(
                    pixi_setup,
                    "_json_command",
                    side_effect=[
                        {"product": "Workbench"},
                        {"summary": {"status": "invented"}},
                    ],
                ),
                patch.object(
                    pixi_setup,
                    "_verified_pixi_tool",
                    return_value=self.PIXI_TOOL,
                ),
            ):
                with self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "unknown status",
                ):
                    pixi_setup.build_setup_result(
                        Path(temporary),
                        provision_java=False,
                    )

    def test_pixi_executable_must_report_the_manifest_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "pixi"
            executable.write_bytes(b"pixi fixture")
            executable.chmod(0o755)
            completed = subprocess.CompletedProcess(
                [str(executable), "--version"],
                0,
                stdout="pixi 0.74.0\n",
                stderr="",
            )
            with (
                patch.dict(
                    os.environ,
                    {"PIXI_EXE": str(executable)},
                    clear=False,
                ),
                patch.object(
                    pixi_setup.subprocess,
                    "run",
                    return_value=completed,
                ),
                self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "must report exactly `pixi 0.75.0`",
                ),
            ):
                pixi_setup._verified_pixi_tool("==0.75.0")

    def test_pixi_executable_mutation_between_probe_and_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "pixi"
            executable.write_bytes(b"initial pixi fixture")
            executable.chmod(0o755)

            def mutate_staged(command: list[str], **_kwargs: object):
                staged = Path(command[0])
                staged.chmod(0o700)
                staged.write_bytes(b"replacement pixi fixture")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="pixi 0.75.0\n",
                    stderr="",
                )

            with (
                patch.dict(os.environ, {"PIXI_EXE": str(executable)}, clear=False),
                patch.object(pixi_setup.subprocess, "run", side_effect=mutate_staged),
                self.assertRaisesRegex(
                    pixi_setup.PixiSetupError,
                    "staged executable bytes changed",
                ),
            ):
                pixi_setup._verified_pixi_tool("==0.75.0")


if __name__ == "__main__":
    unittest.main()
