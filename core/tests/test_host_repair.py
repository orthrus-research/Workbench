"""Linux/Windows coverage for host discovery and reviewed repair."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from contextlib import redirect_stdout
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parent
PROJECT_INTELLIGENCE_SOURCE = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core import host_requirements
from workbench_core import repair_cli
from workbench_core import setup_cli  # noqa: E402


def _no_which(_name: str, path: str = "") -> None:
    del path
    return None


def _write_fake_git(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf 'git version 2.fixture\\n'\n", encoding="utf-8")
    path.chmod(0o755)


def _stat_view_with_mode(observed: os.stat_result, mode: int) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
    )


class _TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def _packaged_environment_status() -> dict[str, object]:
    return {
        "state": "ready",
        "execution": {"mode": "packaged-pixi", "source_checkout": False},
        "pixi": {
            "state": "unavailable",
            "version": None,
            "required_constraint": None,
            "matches_required_constraint": None,
            "executable": None,
        },
    }


class HostRequirementTests(unittest.TestCase):
    def test_linux_candidates_include_standard_directories_without_path(self) -> None:
        host = host_requirements.inspect_host(
            system_name="Linux",
            machine="x86_64",
            os_release_path="/does/not/exist",
        )
        candidates = host_requirements.git_candidates(
            {"PATH": ""},
            host,
            which=_no_which,
        )
        paths = [row["path"] for row in candidates]

        self.assertIn("/usr/local/bin/git", paths)
        self.assertIn("/usr/bin/git", paths)
        self.assertIn("/bin/git", paths)
        self.assertEqual("linux", host["family"])
        self.assertTrue(host["repair_family_recognized"])

    def test_linux_system_candidates_survive_missing_home_and_runtime_path(self) -> None:
        host = host_requirements.inspect_host(
            system_name="Linux",
            machine="x86_64",
            os_release_path="/does/not/exist",
        )
        with (
            patch.object(host_requirements.sys, "executable", ""),
            patch.object(host_requirements.Path, "home", side_effect=RuntimeError),
        ):
            candidates = host_requirements.git_candidates(
                {"PATH": ""},
                host,
                which=_no_which,
            )

        paths = [row["path"] for row in candidates]
        self.assertIn("/usr/local/bin/git", paths)
        self.assertIn("/usr/bin/git", paths)
        self.assertNotIn("workbench-runtime", {row["source"] for row in candidates})

    def test_relative_and_empty_path_entries_cannot_select_git(self) -> None:
        host = host_requirements.inspect_host(
            system_name="Linux",
            machine="x86_64",
            os_release_path="/does/not/exist",
        )
        calls: list[str] = []

        def unsafe_lookup(_name: str, path: str = "") -> str:
            calls.append(path)
            return "git"

        candidates = host_requirements.git_candidates(
            {"PATH": os.pathsep.join(("", ".", "relative/bin"))},
            host,
            which=unsafe_lookup,
        )

        self.assertEqual([], calls)
        self.assertNotIn("PATH", {row["source"] for row in candidates})

    def test_windows_program_files_git_is_discovered_when_path_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            program_files = Path(temporary) / "Program Files"
            git = program_files / "Git/cmd/git.exe"
            _write_fake_git(git)

            check = host_requirements.inspect_host_requirements(
                environment={"PATH": "", "ProgramFiles": str(program_files)},
                system_name="Windows",
                machine="AMD64",
                which=_no_which,
            )

            requirement = check["requirements"]["git"]
            self.assertEqual("ready", check["state"])
            self.assertEqual(str(git), requirement["executable"])
            self.assertEqual("windows-program-files", requirement["discovery"])
            self.assertEqual("git version 2.fixture", requirement["version"])

    def test_windows_program_files_precedes_packaged_path_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program_files = root / "Program Files"
            host_git = program_files / "Git/cmd/git.exe"
            packaged_git = root / "pixi-runtime/Library/bin/git.exe"
            _write_fake_git(host_git)
            _write_fake_git(packaged_git)

            def packaged_lookup(_name: str, path: str = "") -> str:
                self.assertEqual(str(packaged_git.parent), path)
                return str(packaged_git)

            check = host_requirements.inspect_host_requirements(
                environment={
                    "PATH": str(packaged_git.parent),
                    "ProgramFiles": str(program_files),
                },
                system_name="Windows",
                machine="AMD64",
                which=packaged_lookup,
            )

            requirement = check["requirements"]["git"]
            self.assertEqual("ready", check["state"])
            self.assertEqual(str(host_git), requirement["executable"])
            self.assertEqual("windows-program-files", requirement["discovery"])

    def test_windows_environment_lookup_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            program_files = Path(temporary) / "Program Files"
            git = program_files / "Git/cmd/git.exe"
            _write_fake_git(git)

            check = host_requirements.inspect_host_requirements(
                environment={"path": "", "PROGRAMFILES": str(program_files)},
                system_name="Windows",
                machine="AMD64",
                which=_no_which,
            )

            requirement = check["requirements"]["git"]
            self.assertEqual("ready", check["state"])
            self.assertEqual(str(git), requirement["executable"])

    def test_windows_common_directory_roots_must_be_absolute(self) -> None:
        host = host_requirements.inspect_host(
            system_name="Windows",
            machine="AMD64",
        )
        candidates = host_requirements.git_candidates(
            {
                "PATH": "",
                "ProgramFiles": "relative-program-files",
                "LOCALAPPDATA": "relative-local-data",
                "USERPROFILE": "relative-profile",
                "ChocolateyInstall": "relative-chocolatey",
            },
            host,
            which=_no_which,
        )

        sources = {row["source"] for row in candidates}
        self.assertNotIn("windows-local-app-data", sources)
        self.assertNotIn("windows-chocolatey", sources)
        self.assertNotIn("windows-scoop", sources)

    def test_debian_repair_uses_exact_apt_command_and_sudo_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            os_release = parent / "os-release"
            os_release.write_text("ID=debian\nID_LIKE=debian\n", encoding="utf-8")
            apt = parent / "apt-get"
            sudo = parent / "sudo"
            for executable in (apt, sudo):
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)

            host = host_requirements.inspect_host(
                system_name="Linux",
                machine="x86_64",
                os_release_path=os_release,
            )
            strategy = host_requirements.git_install_strategy(
                host,
                {"PATH": ""},
                effective_uid=1000,
                manager_paths={"apt-get": (apt,)},
                sudo_paths=(sudo,),
            )

            self.assertEqual("available", strategy["state"])
            self.assertEqual("apt-get", strategy["manager"])
            self.assertEqual(
                [str(sudo), "--", str(apt), "install", "--yes", "git"],
                strategy["command"],
            )
            self.assertTrue(strategy["requires_elevation"])

    def test_windows_repair_prefers_winget_with_noninteractive_agreements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            winget = Path(temporary) / "winget.exe"
            winget.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            winget.chmod(0o755)

            host = host_requirements.inspect_host(
                system_name="Windows",
                machine="AMD64",
            )
            strategy = host_requirements.git_install_strategy(
                host,
                {"PATH": ""},
                manager_paths={"winget": (winget,), "chocolatey": ()},
            )

            self.assertEqual("available", strategy["state"])
            self.assertEqual("winget", strategy["manager"])
            self.assertEqual(str(winget), strategy["command"][0])
            self.assertIn("Git.Git", strategy["command"])
            self.assertIn("--accept-package-agreements", strategy["command"])
            self.assertIn("--accept-source-agreements", strategy["command"])

    def test_windows_repair_accepts_standard_zero_byte_winget_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "Local"
            winget = local / "Microsoft/WindowsApps/winget.exe"
            winget.parent.mkdir(parents=True)
            winget.touch()

            host = host_requirements.inspect_host(
                system_name="Windows",
                machine="AMD64",
            )
            strategy = host_requirements.git_install_strategy(
                host,
                {"PATH": "", "LOCALAPPDATA": str(local)},
                manager_paths={"winget": (winget,), "chocolatey": ()},
            )

            self.assertEqual("available", strategy["state"])
            self.assertEqual("winget", strategy["manager"])
            self.assertEqual(0, strategy["executable_identities"][0]["size_bytes"])
            self.assertEqual(str(winget), strategy["command"][0])

    def test_windows_executable_measurement_allows_cross_view_mode_difference(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git = Path(temporary) / "Program Files/Git/cmd/git.exe"
            _write_fake_git(git)
            real_fstat = os.fstat

            def windows_descriptor_stat(descriptor: int) -> SimpleNamespace:
                observed = real_fstat(descriptor)
                descriptor_mode = stat.S_IFREG | 0o700
                self.assertNotEqual(observed.st_mode, descriptor_mode)
                return _stat_view_with_mode(observed, descriptor_mode)

            with (
                patch.object(
                    host_requirements,
                    "_WINDOWS_DESCRIPTOR_PATH_MODE_MAY_DIFFER",
                    True,
                ),
                patch.object(
                    host_requirements.os,
                    "fstat",
                    side_effect=windows_descriptor_stat,
                ),
            ):
                identity = host_requirements.measure_executable(git)

            self.assertEqual(str(git), identity["path"])
            self.assertEqual(0o700, identity["mode"])

            with (
                patch.object(
                    host_requirements,
                    "_WINDOWS_DESCRIPTOR_PATH_MODE_MAY_DIFFER",
                    False,
                ),
                patch.object(
                    host_requirements.os,
                    "fstat",
                    side_effect=windows_descriptor_stat,
                ),
                self.assertRaisesRegex(ValueError, "changed while it was measured"),
            ):
                host_requirements.measure_executable(git)

    def test_windows_executable_measurement_rejects_non_regular_path_view(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git = Path(temporary) / "Program Files/Git/cmd/git.exe"
            _write_fake_git(git)
            resolved = git.resolve(strict=True)
            real_lstat = Path.lstat

            def symlink_path_view(path: Path) -> SimpleNamespace | os.stat_result:
                observed = real_lstat(path)
                if path == resolved:
                    return _stat_view_with_mode(observed, stat.S_IFLNK | 0o777)
                return observed

            with (
                patch.object(
                    host_requirements,
                    "_WINDOWS_DESCRIPTOR_PATH_MODE_MAY_DIFFER",
                    True,
                ),
                patch.object(host_requirements.Path, "lstat", symlink_path_view),
                self.assertRaisesRegex(ValueError, "changed while it was measured"),
            ):
                host_requirements.measure_executable(git)

    def test_executable_measurement_does_not_recheck_path_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git = Path(temporary) / "git"
            _write_fake_git(git)
            with patch.object(
                host_requirements.os,
                "access",
                side_effect=AssertionError("pathname access check is forbidden"),
            ):
                identity = host_requirements.measure_executable(git)
            self.assertEqual(str(git), identity["path"])

    def test_windows_arm64_automatic_git_install_remains_manual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            winget = Path(temporary) / "Microsoft/WindowsApps/winget.exe"
            winget.parent.mkdir(parents=True)
            winget.touch()
            host = host_requirements.inspect_host(
                system_name="Windows",
                machine="ARM64",
            )

            strategy = host_requirements.git_install_strategy(
                host,
                {"PATH": ""},
                manager_paths={"winget": (winget,), "chocolatey": ()},
            )

            self.assertEqual("manual", strategy["state"])
            self.assertIsNone(strategy["command"])
            self.assertIn("ARM64", strategy["detail"])

    def test_chocolatey_requires_an_elevated_windows_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            choco = Path(temporary) / "choco.exe"
            choco.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            choco.chmod(0o755)
            host = host_requirements.inspect_host(
                system_name="Windows",
                machine="AMD64",
            )

            manual = host_requirements.git_install_strategy(
                host,
                {"PATH": ""},
                manager_paths={"winget": (), "chocolatey": (choco,)},
                windows_is_elevated=False,
            )
            available = host_requirements.git_install_strategy(
                host,
                {"PATH": ""},
                manager_paths={"winget": (), "chocolatey": (choco,)},
                windows_is_elevated=True,
            )

            self.assertEqual("manual", manual["state"])
            self.assertIsNone(manual["command"])
            self.assertIn("administrator", manual["detail"])
            self.assertEqual("available", available["state"])
            self.assertTrue(available["requires_elevation"])

    def test_setup_git_probe_uses_only_bounded_host_discovery(self) -> None:
        missing_check = {
            "requirements": {
                "git": {"state": "missing", "executable": None},
            }
        }
        hostile_path = os.pathsep.join(("", ".", "relative/bin"))
        with patch.object(
            setup_cli,
            "inspect_host_requirements",
            return_value=missing_check,
        ) as inspect:
            result = setup_cli._probe_git(
                None,
                {"PATH": hostile_path},
                required=True,
            )

        inspect.assert_called_once_with(environment={"PATH": hostile_path})
        self.assertEqual("missing-manual", result["state"])

    def test_linux_repair_never_selects_package_tools_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            malicious = parent / "bin"
            malicious.mkdir()
            for name in ("apt-get", "sudo"):
                executable = malicious / name
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            host = host_requirements.inspect_host(
                system_name="Linux",
                machine="x86_64",
                os_release_path="/does/not/exist",
            )

            strategy = host_requirements.git_install_strategy(
                host,
                {"PATH": str(malicious)},
                effective_uid=1000,
                manager_paths={
                    "apt-get": (),
                    "dnf": (),
                    "yum": (),
                    "zypper": (),
                    "pacman": (),
                },
                sudo_paths=(),
            )

            self.assertEqual("manual", strategy["state"])
            self.assertIsNone(strategy["command"])

    def test_invalid_explicit_git_is_a_blocker_not_an_install_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            missing = parent / "missing-git"
            with patch.object(
                host_requirements,
                "git_install_strategy",
            ) as install_strategy:
                check = host_requirements.inspect_host_requirements(
                    environment={"PATH": ""},
                    explicit_git=missing,
                    system_name="Linux",
                    machine="x86_64",
                    which=_no_which,
                )

            repair_check = {
                "format": repair_cli.CHECK_FORMAT,
                "schema_version": 1,
                "operation_class": "read-only",
                "state": check["state"],
                "host": check["host"],
                "setup": {
                    "record_path": str(parent / "setup-v1.json"),
                    "record_id": None,
                    "state": "not-configured",
                    "git_binding": "not-configured",
                },
                "requirements": check["requirements"],
            }
            plan = repair_cli.build_repair_plan(repair_check)

            install_strategy.assert_not_called()
            self.assertEqual(["git"], plan["blockers"])
            self.assertFalse(any(row["id"] == "install-git" for row in plan["actions"]))

    def test_human_repair_uses_semantic_color_from_the_injected_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)
            output = _TtyBuffer()

            status = repair_cli.main(
                ["--check", "--git-executable", str(git)],
                output=output,
                error=io.StringIO(),
                environment={},
                record_path=parent / "setup-v1.json",
            )

            self.assertEqual(0, status)
            self.assertIn("\x1b[32m[READY]\x1b[0m", output.getvalue())
            self.assertIn("\x1b[33m[OPTIONAL]\x1b[0m", output.getvalue())
            self.assertNotIn("\x1b[31m", output.getvalue())

            binding_check = repair_cli.inspect_repair(
                environment={},
                record_path=parent / "setup-v1.json",
                explicit_git=git,
            )
            binding_check["state"] = "repairable"
            binding_check["setup"]["state"] = "configured"
            binding_check["setup"]["git_binding"] = "repair-needed"
            binding_output = _TtyBuffer()
            with patch.object(
                repair_cli,
                "inspect_repair",
                return_value=binding_check,
            ):
                binding_status = repair_cli.main(
                    ["--check", "--git-executable", str(git)],
                    output=binding_output,
                    error=io.StringIO(),
                    environment={},
                    record_path=parent / "setup-v1.json",
                )

            self.assertEqual(1, binding_status)
            self.assertIn(
                "Setup: configured · Git binding repair needed",
                binding_output.getvalue(),
            )
            self.assertIn(
                "\x1b[33m[ATTENTION]\x1b[0m",
                binding_output.getvalue(),
            )

            missing_output = _TtyBuffer()
            missing_status = repair_cli.main(
                [
                    "--check",
                    "--git-executable",
                    str(parent / "missing-git"),
                ],
                output=missing_output,
                error=io.StringIO(),
                environment={},
                record_path=parent / "setup-v1.json",
            )

            self.assertEqual(1, missing_status)
            self.assertIn(
                "\x1b[31m[MISSING]\x1b[0m",
                missing_output.getvalue(),
            )

    def test_human_repair_honors_no_color_and_redirected_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)

            no_color = _TtyBuffer()
            no_color_status = repair_cli.main(
                ["--check", "--git-executable", str(git)],
                output=no_color,
                error=io.StringIO(),
                environment={"NO_COLOR": ""},
                record_path=parent / "setup-v1.json",
            )
            redirected = io.StringIO()
            with patch.object(repair_cli.sys, "stdout", _TtyBuffer()):
                redirected_status = repair_cli.main(
                    ["--check", "--git-executable", str(git)],
                    output=redirected,
                    error=io.StringIO(),
                    environment={},
                    record_path=parent / "setup-v1.json",
                )

            self.assertEqual(0, no_color_status)
            self.assertEqual(0, redirected_status)
            self.assertNotIn("\x1b[", no_color.getvalue())
            self.assertNotIn("\x1b[", redirected.getvalue())
            self.assertIn("[READY]", no_color.getvalue())
            self.assertIn("[READY]", redirected.getvalue())

    def test_repair_json_stays_unstyled_on_a_tty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)
            output = _TtyBuffer()

            status = repair_cli.main(
                ["--check", "--json", "--git-executable", str(git)],
                output=output,
                error=io.StringIO(),
                environment={},
                record_path=parent / "setup-v1.json",
            )

            self.assertEqual(0, status)
            self.assertNotIn("\x1b[", output.getvalue())
            self.assertEqual(
                repair_cli.CHECK_FORMAT,
                json.loads(output.getvalue())["format"],
            )

    def test_git_probe_rejects_truncated_or_timed_out_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git = Path(temporary) / "git"
            _write_fake_git(git)

            with patch.object(
                host_requirements,
                "_bounded_command_output",
                return_value=(0, b"git version 2.fixture", True, None),
            ):
                truncated = host_requirements.probe_git_executable(git)
            with patch.object(
                host_requirements,
                "_bounded_command_output",
                return_value=(-9, b"", False, "TimeoutExpired"),
            ):
                timed_out = host_requirements.probe_git_executable(git)

            self.assertEqual("incompatible", truncated["state"])
            self.assertIn("exceeded", truncated["detail"])
            self.assertEqual(
                {"state": "incompatible", "detail": "TimeoutExpired"},
                timed_out,
            )

    def test_git_probe_rejects_multiline_and_control_character_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git = Path(temporary) / "git"
            _write_fake_git(git)

            with patch.object(
                host_requirements,
                "_bounded_command_output",
                return_value=(0, b"git version 2.fixture\nextra", False, None),
            ):
                multiline = host_requirements.probe_git_executable(git)
            with patch.object(
                host_requirements,
                "_bounded_command_output",
                return_value=(0, b"git version 2.fixture\x00", False, None),
            ):
                control = host_requirements.probe_git_executable(git)

            self.assertEqual("incompatible", multiline["state"])
            self.assertEqual("incompatible", control["state"])

    def test_repair_rebinds_existing_setup_after_exact_plan_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "workspace"
            workspace.mkdir()
            git = parent / "tools/git"
            _write_fake_git(git)
            record_path = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            original = setup_cli._write_setup_record(record_path, selection)

            planned_output = io.StringIO()
            planned_error = io.StringIO()
            planned = repair_cli.main(
                ["--plan", "--json", "--git-executable", str(git)],
                output=planned_output,
                error=planned_error,
                environment={"PATH": ""},
                record_path=record_path,
            )
            plan = json.loads(planned_output.getvalue())

            self.assertEqual(0, planned)
            self.assertEqual("", planned_error.getvalue())
            self.assertEqual("repairable", repair_cli.inspect_repair(
                environment={"PATH": ""},
                record_path=record_path,
                explicit_git=git,
            )["state"])
            self.assertEqual(["bind-existing-git"], [row["id"] for row in plan["actions"]])
            self.assertTrue(plan["consent"]["required"])

            applied_output = io.StringIO()
            applied_error = io.StringIO()
            applied = repair_cli.main(
                [
                    "--apply",
                    plan["plan_id"],
                    "--json",
                    "--git-executable",
                    str(git),
                ],
                output=applied_output,
                error=applied_error,
                environment={"PATH": ""},
                record_path=record_path,
            )
            result = json.loads(applied_output.getvalue())
            repaired = setup_cli.load_setup_record(record_path)

            self.assertEqual(0, applied)
            self.assertEqual("", applied_error.getvalue())
            self.assertEqual("repaired", result["outcome"])
            self.assertEqual(str(git), repaired["selection"]["git_executable"])
            self.assertNotEqual(original["record_id"], repaired["record_id"])

    def test_invalid_setup_record_is_quarantined_recoverably_after_consent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "tools/git"
            _write_fake_git(git)
            record_path = parent / "config/setup-v1.json"
            record_path.parent.mkdir()
            invalid_bytes = b'{"format": "broken"\n'
            record_path.write_bytes(invalid_bytes)

            planned_output = io.StringIO()
            planned_error = io.StringIO()
            planned = repair_cli.main(
                ["--plan", "--json", "--git-executable", str(git)],
                output=planned_output,
                error=planned_error,
                environment={"PATH": ""},
                record_path=record_path,
            )
            plan = json.loads(planned_output.getvalue())
            recovery = next(
                row for row in plan["actions"] if row["id"] == "quarantine-invalid-setup"
            )

            self.assertEqual(0, planned)
            self.assertEqual("", planned_error.getvalue())
            self.assertEqual("ready", plan["state"])
            self.assertTrue(recovery["destination"].endswith(".bak"))

            applied_output = io.StringIO()
            applied_error = io.StringIO()
            applied = repair_cli.main(
                [
                    "--apply",
                    plan["plan_id"],
                    "--json",
                    "--git-executable",
                    str(git),
                ],
                output=applied_output,
                error=applied_error,
                environment={"PATH": ""},
                record_path=record_path,
            )
            result = json.loads(applied_output.getvalue())
            backup = Path(result["recovered_setup_record"])

            self.assertEqual(0, applied)
            self.assertEqual("", applied_error.getvalue())
            self.assertEqual("repaired", result["outcome"])
            self.assertFalse(record_path.exists())
            self.assertEqual(invalid_bytes, backup.read_bytes())
            self.assertEqual([["workbench", "setup"]], result["next_commands"])

    @unittest.skipIf(os.name == "nt", "symlink creation is not uniformly available on Windows")
    def test_invalid_setup_symlink_is_moved_without_touching_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)
            target = parent / "retained-target.json"
            retained = b'{"owner": "user"}\n'
            target.write_bytes(retained)
            record_path = parent / "config/setup-v1.json"
            record_path.parent.mkdir()
            record_path.symlink_to(target)

            check = repair_cli.inspect_repair(
                environment={"PATH": ""},
                record_path=record_path,
                explicit_git=git,
            )
            plan = repair_cli.build_repair_plan(check)
            result = repair_cli._apply_plan(
                plan,
                environment={"PATH": ""},
                explicit_git=git,
                output=io.StringIO(),
            )
            backup = Path(result["recovered_setup_record"])

            self.assertFalse(record_path.exists())
            self.assertTrue(backup.is_symlink())
            self.assertEqual(target, backup.resolve())
            self.assertEqual(retained, target.read_bytes())

    def test_oversized_invalid_setup_record_remains_a_manual_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)
            record_path = parent / "config/setup-v1.json"
            record_path.parent.mkdir()
            record_path.write_bytes(b"x" * (repair_cli.MAX_INVALID_SETUP_BYTES + 1))

            check = repair_cli.inspect_repair(
                environment={"PATH": ""},
                record_path=record_path,
                explicit_git=git,
            )
            plan = repair_cli.build_repair_plan(check)

            self.assertEqual("attention", check["state"])
            self.assertEqual(["setup-record"], plan["blockers"])
            self.assertFalse(
                any(row["id"] == "quarantine-invalid-setup" for row in plan["actions"])
            )

    def test_rebinding_uses_a_locked_compare_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "workspace"
            workspace.mkdir()
            git = parent / "git"
            _write_fake_git(git)
            record_path = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            original = setup_cli._write_setup_record(record_path, selection)
            waiting = threading.Event()
            allow_lock = threading.Event()
            real_lock = setup_cli.setup_record_lock

            @setup_cli.contextmanager
            def controlled_lock(path: Path | str):
                waiting.set()
                self.assertTrue(allow_lock.wait(timeout=2))
                with real_lock(path):
                    yield

            errors: list[BaseException] = []

            def rebind() -> None:
                try:
                    setup_cli.replace_setup_git_binding(
                        record_path,
                        expected_record_id=original["record_id"],
                        git_executable=git,
                    )
                except BaseException as exc:  # captured for assertion in the main thread
                    errors.append(exc)

            with patch.object(setup_cli, "setup_record_lock", controlled_lock):
                worker = threading.Thread(target=rebind)
                worker.start()
                self.assertTrue(waiting.wait(timeout=2))
                changed = dict(selection)
                changed["state_root"] = str(parent / "other-state")
                setup_cli._write_setup_record_unlocked(record_path, changed)
                allow_lock.set()
                worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], setup_cli.SetupError)
            self.assertIn("changed after", str(errors[0]))
            self.assertEqual(
                str(parent / "other-state"),
                setup_cli.load_setup_record(record_path)["selection"]["state_root"],
            )

    def test_rebinding_rejects_git_identity_drift_before_record_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "workspace"
            workspace.mkdir()
            git = parent / "git"
            _write_fake_git(git)
            expected_git = host_requirements.measure_executable(git)
            record_path = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            original = setup_cli._write_setup_record(record_path, selection)
            git.write_text(
                "#!/bin/sh\nprintf 'git version 3.changed\\n'\n",
                encoding="utf-8",
            )
            git.chmod(0o755)

            with self.assertRaisesRegex(setup_cli.SetupError, "changed after"):
                setup_cli.replace_setup_git_binding(
                    record_path,
                    expected_record_id=original["record_id"],
                    git_executable=git,
                    expected_git_identity=expected_git,
                )

            self.assertEqual(original, setup_cli.load_setup_record(record_path))

    def test_changed_package_manager_identity_fails_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            manager = parent / "manager"
            manager.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            manager.chmod(0o755)
            identity = host_requirements.measure_executable(manager)
            plan = {
                "blockers": [],
                "plan_id": "workbench-repair-plan:fixture",
                "setup": {
                    "record_path": str(parent / "setup-v1.json"),
                    "record_id": None,
                },
                "actions": [
                    {
                        "id": "install-git",
                        "source": "fixture",
                        "command": [str(manager)],
                        "executable_identities": [identity],
                    }
                ],
            }
            manager.write_text("#!/bin/sh\nprintf changed\n", encoding="utf-8")
            manager.chmod(0o755)

            with patch.object(repair_cli.subprocess, "run") as run:
                with self.assertRaisesRegex(
                    repair_cli.RepairError,
                    "changed after plan review",
                ):
                    repair_cli._apply_plan(
                        plan,
                        environment={"PATH": ""},
                        explicit_git=None,
                        output=io.StringIO(),
                    )
            run.assert_not_called()

    def test_reused_git_identity_drift_fails_before_setup_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)
            expected = host_requirements.measure_executable(git)
            plan = {
                "blockers": [],
                "plan_id": "workbench-repair-plan:fixture",
                "setup": {
                    "record_path": str(parent / "setup-v1.json"),
                    "record_id": None,
                },
                "requirements": {
                    "git": {"executable_identity": expected},
                },
                "actions": [{"id": "verify-existing-git"}],
            }
            git.write_text("#!/bin/sh\nprintf 'git version 3.changed\\n'\n", encoding="utf-8")
            git.chmod(0o755)

            with self.assertRaisesRegex(
                repair_cli.RepairError,
                "Git executable changed after plan review",
            ):
                repair_cli._apply_plan(
                    plan,
                    environment={"PATH": ""},
                    explicit_git=git,
                    output=io.StringIO(),
                )

    def test_failed_package_command_returns_a_structured_partial_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            manager = parent / "manager"
            manager.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            manager.chmod(0o755)
            check = {
                "format": repair_cli.CHECK_FORMAT,
                "schema_version": 1,
                "operation_class": "read-only",
                "state": "repairable",
                "host": {
                    "family": "linux",
                    "system": "Linux",
                    "machine": "x86_64",
                    "repair_family_recognized": True,
                    "distribution": {"id": "fixture", "id_like": None},
                },
                "setup": {
                    "record_path": str(parent / "setup-v1.json"),
                    "record_id": None,
                    "state": "not-configured",
                    "git_binding": "not-configured",
                    "error": None,
                    "invalid_observation": None,
                    "recovery_path": None,
                },
                "requirements": {
                    "git": {
                        "state": "missing",
                        "executable": None,
                        "version": None,
                        "discovery": None,
                        "executable_identity": None,
                        "candidates_checked": [],
                        "detail": "missing",
                        "repair": {
                            "state": "available",
                            "manager": "fixture",
                            "requires_elevation": False,
                            "command": [str(manager)],
                            "executable_identities": [
                                host_requirements.measure_executable(manager)
                            ],
                            "detail": "Install Git.",
                        },
                    }
                },
            }
            plan = repair_cli.build_repair_plan(check)
            output = io.StringIO()
            error = io.StringIO()

            with patch.object(repair_cli, "inspect_repair", return_value=check):
                status = repair_cli.main(
                    ["--apply", plan["plan_id"], "--json"],
                    output=output,
                    error=error,
                    environment={"PATH": ""},
                    record_path=parent / "setup-v1.json",
                )
            result = json.loads(output.getvalue())

            self.assertEqual(2, status)
            self.assertEqual("partial", result["outcome"])
            self.assertEqual("failed-exit-7", result["installed"][0]["outcome"])
            self.assertIn("failed with exit 7", result["failure"])
            self.assertIn("Workbench repair incomplete", error.getvalue())

    def test_structured_apply_keeps_package_output_off_json_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            manager = parent / "manager"
            manager.write_text(
                "#!/bin/sh\nprintf 'package chatter\\n'\n",
                encoding="utf-8",
            )
            manager.chmod(0o755)
            git = parent / "git"
            _write_fake_git(git)
            plan = {
                "blockers": [],
                "plan_id": "workbench-repair-plan:fixture",
                "setup": {
                    "record_path": str(parent / "setup-v1.json"),
                    "record_id": None,
                },
                "actions": [
                    {
                        "id": "install-git",
                        "source": "fixture",
                        "command": [str(manager)],
                        "executable_identities": [
                            host_requirements.measure_executable(manager)
                        ],
                    }
                ],
            }
            progress = io.StringIO()
            leaked_stdout = io.StringIO()

            with redirect_stdout(leaked_stdout):
                result = repair_cli._apply_plan(
                    plan,
                    environment={"PATH": ""},
                    explicit_git=git,
                    output=progress,
                    structured_output=True,
                )

            self.assertEqual("", leaked_stdout.getvalue())
            self.assertIn("package chatter", progress.getvalue())
            self.assertEqual("ready", result["git"]["state"])

    def test_structured_package_capture_retains_only_a_bounded_tail(self) -> None:
        returncode, output, truncated, timed_out = (
            repair_cli._run_bounded_package_command(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.stdout.buffer.write(b'x' * 131072 + b'END')"
                    ),
                ],
                environment=None,
            )
        )

        self.assertEqual(0, returncode)
        self.assertTrue(truncated)
        self.assertFalse(timed_out)
        self.assertEqual(repair_cli.MAX_PACKAGE_OUTPUT_BYTES, len(output))
        self.assertTrue(output.endswith(b"END"))

    def test_first_setup_binds_preflight_git_and_uses_existing_required_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "workspace"
            workspace.mkdir()
            git = parent / "Program Files/Git/cmd/git.exe"
            host_check = {
                "state": "ready",
                "host": {
                    "family": "windows",
                    "system": "Windows",
                    "machine": "AMD64",
                },
                "requirements": {
                    "git": {
                        "state": "ready",
                        "executable": str(git),
                        "version": "git version 2.fixture",
                        "discovery": "windows-program-files",
                    }
                },
            }
            output = io.StringIO()
            error = io.StringIO()
            with (
                patch.object(
                    setup_cli,
                    "inspect_host_requirements",
                    return_value=host_check,
                ),
                patch.object(
                    setup_cli,
                    "inspect_environment_status",
                    return_value=_packaged_environment_status(),
                ),
                patch.object(
                    setup_cli,
                    "_probe_git",
                    side_effect=lambda _selected, _environment, required=False: setup_cli._dependency(
                        "git",
                        "Git",
                        "ready",
                        "git version 2.fixture",
                        source=str(git),
                        required=required,
                    ),
                ),
            ):
                status = setup_cli.main(
                    ["--plan", "--json", "--workspace", str(workspace)],
                    root=REPOSITORY_ROOT,
                    output=output,
                    error=error,
                    environment={"PATH": ""},
                    record_path=parent / "config/setup-v1.json",
                )
            plan = json.loads(output.getvalue())
            git_row = next(row for row in plan["dependencies"] if row["id"] == "git")

            self.assertEqual(0, status)
            self.assertEqual("", error.getvalue())
            self.assertEqual(str(git), plan["selection"]["git_executable"])
            self.assertTrue(git_row["required"])

    def test_first_setup_stops_at_host_repair_when_git_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "workspace"
            workspace.mkdir()
            host_check = {
                "state": "repairable",
                "host": {
                    "family": "linux",
                    "system": "Linux",
                    "machine": "x86_64",
                },
                "requirements": {
                    "git": {
                        "state": "missing",
                        "executable": None,
                        "repair": {"state": "available"},
                    }
                },
            }
            output = io.StringIO()
            error = io.StringIO()
            with (
                patch.object(
                    setup_cli,
                    "inspect_host_requirements",
                    return_value=host_check,
                ),
                patch.object(
                    setup_cli,
                    "inspect_environment_status",
                    return_value=_packaged_environment_status(),
                ),
            ):
                status = setup_cli.main(
                    ["--plan", "--json", "--workspace", str(workspace)],
                    root=REPOSITORY_ROOT,
                    output=output,
                    error=error,
                    environment={"PATH": ""},
                    record_path=parent / "config/setup-v1.json",
                )

            plan = json.loads(output.getvalue())
            git = next(row for row in plan["dependencies"] if row["id"] == "git")
            self.assertEqual(0, status)
            self.assertEqual("", error.getvalue())
            self.assertEqual("blocked", plan["state"])
            self.assertEqual(["git"], plan["blockers"])
            self.assertTrue(git["required"])
            self.assertIn("workbench repair", git["repair"])
            self.assertFalse((parent / "config/setup-v1.json").exists())

    def test_first_setup_never_executes_git_from_a_relative_path_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "workspace"
            workspace.mkdir()
            fake_git = parent / "git"
            marker = parent / "fake-git-ran"
            fake_git.write_text(
                f"#!/bin/sh\ntouch {marker}\nprintf 'git version fake\\n'\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            output = io.StringIO()
            error = io.StringIO()
            previous = Path.cwd()
            try:
                os.chdir(parent)
                with (
                    patch.object(
                        host_requirements.platform,
                        "system",
                        return_value="OtherOS",
                    ),
                    patch.object(host_requirements.sys, "executable", ""),
                    patch.object(
                        setup_cli,
                        "inspect_environment_status",
                        return_value=_packaged_environment_status(),
                    ),
                ):
                    status = setup_cli.main(
                        ["--plan", "--json", "--workspace", str(workspace)],
                        root=REPOSITORY_ROOT,
                        output=output,
                        error=error,
                        environment={"PATH": "."},
                        record_path=parent / "config/setup-v1.json",
                    )
            finally:
                os.chdir(previous)

            plan = json.loads(output.getvalue())
            self.assertEqual(0, status)
            self.assertEqual("blocked", plan["state"])
            self.assertEqual(["git"], plan["blockers"])
            self.assertFalse(marker.exists())

    def test_public_router_exposes_repair_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(REPOSITORY_ROOT / "tools/workbench.py"), "repair", "--help"],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("usage: workbench repair", completed.stdout)
        self.assertIn("standard Linux and Windows Git locations", completed.stdout)


if __name__ == "__main__":
    unittest.main()
