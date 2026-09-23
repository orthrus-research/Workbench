"""Schema coverage for Workbench host-repair check, plan, and result records."""

from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_SOURCE = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core import host_requirements
from workbench_core import repair_cli  # noqa: E402


SCHEMA_PATHS = {
    "check": MODULE_ROOT / "schemas/workbench-repair-check-v1.schema.json",
    "plan": MODULE_ROOT / "schemas/workbench-repair-plan-v1.schema.json",
    "result": MODULE_ROOT / "schemas/workbench-repair-result-v1.schema.json",
}


def _write_fake_git(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf 'git version 2.fixture\\n'\n", encoding="utf-8")
    path.chmod(0o755)


class HostRepairSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in SCHEMA_PATHS.items()
        }
        registry = Registry()
        for schema in cls.schemas.values():
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(
                schema["$id"],
                Resource.from_contents(schema),
            )
        cls.validators = {
            name: Draft202012Validator(schema, registry=registry)
            for name, schema in cls.schemas.items()
        }

    def _validate(self, kind: str, payload: object) -> None:
        self.validators[kind].validate(payload)

    def _ready_payloads(self, parent: Path) -> tuple[dict, dict, dict]:
        git = parent / "tools/git"
        _write_fake_git(git)
        record_path = parent / "config/setup-v1.json"
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
        return check, plan, result

    def test_ready_check_plan_and_result_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            check, plan, result = self._ready_payloads(Path(temporary))

        self.assertEqual("ready", check["state"])
        self.assertEqual(["verify-existing-git"], [row["id"] for row in plan["actions"]])
        self.assertEqual("ready", result["outcome"])
        self._validate("check", check)
        self._validate("plan", plan)
        self._validate("result", result)

    def test_blocked_check_and_plan_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            check = repair_cli.inspect_repair(
                environment={"PATH": ""},
                record_path=parent / "setup-v1.json",
                explicit_git=parent / "missing-git",
            )
            plan = repair_cli.build_repair_plan(check)

        self.assertEqual("attention", check["state"])
        self.assertEqual("blocked", plan["state"])
        self.assertEqual(["git"], plan["blockers"])
        self._validate("check", check)
        self._validate("plan", plan)

    def test_windows_discovery_payloads_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            program_files = parent / "Program Files"
            git = program_files / "Git/cmd/git.exe"
            _write_fake_git(git)
            host_check = host_requirements.inspect_host_requirements(
                environment={"PATH": "", "ProgramFiles": str(program_files)},
                system_name="Windows",
                machine="AMD64",
                which=lambda _name, path="": None,
            )
            with patch.object(
                repair_cli,
                "inspect_host_requirements",
                return_value=host_check,
            ):
                check = repair_cli.inspect_repair(
                    environment={"PATH": "", "ProgramFiles": str(program_files)},
                    record_path=parent / "setup-v1.json",
                )
                plan = repair_cli.build_repair_plan(check)
                result = repair_cli._apply_plan(
                    plan,
                    environment={"PATH": "", "ProgramFiles": str(program_files)},
                    explicit_git=None,
                    output=io.StringIO(),
                )

        self.assertEqual("windows", check["host"]["family"])
        self.assertEqual("windows-program-files", check["requirements"]["git"]["discovery"])
        self._validate("check", check)
        self._validate("plan", plan)
        self._validate("result", result)

    def test_windows_winget_alias_plan_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            local = parent / "Local"
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
            with (
                patch.object(
                    host_requirements,
                    "git_candidates",
                    return_value=[
                        {
                            "path": str(parent / "missing-git.exe"),
                            "source": "windows-program-files",
                        }
                    ],
                ),
                patch.object(
                    host_requirements,
                    "git_install_strategy",
                    return_value=strategy,
                ),
            ):
                host_check = host_requirements.inspect_host_requirements(
                    environment={"PATH": "", "LOCALAPPDATA": str(local)},
                    system_name="Windows",
                    machine="AMD64",
                )
            with patch.object(
                repair_cli,
                "inspect_host_requirements",
                return_value=host_check,
            ):
                check = repair_cli.inspect_repair(
                    environment={"PATH": "", "LOCALAPPDATA": str(local)},
                    record_path=parent / "setup-v1.json",
                )
            plan = repair_cli.build_repair_plan(check)

        identity = plan["actions"][0]["executable_identities"][0]
        self.assertEqual(0, identity["size_bytes"])
        self.assertEqual("winget", check["requirements"]["git"]["repair"]["manager"])
        self._validate("check", check)
        self._validate("plan", plan)

    def test_recoverable_invalid_setup_payloads_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            git = parent / "git"
            _write_fake_git(git)
            record_path = parent / "config/setup-v1.json"
            record_path.parent.mkdir(parents=True)
            record_path.write_bytes(b'{"format": "broken"\n')

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

            self.assertEqual("invalid", check["setup"]["state"])
            self.assertIn(
                "quarantine-invalid-setup",
                [row["id"] for row in plan["actions"]],
            )
            self.assertEqual("repaired", result["outcome"])
            self.assertIsNotNone(result["recovered_setup_record"])
            self._validate("check", check)
            self._validate("plan", plan)
            self._validate("result", result)

    def test_install_failure_partial_result_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            os_release = parent / "os-release"
            os_release.write_text("ID=debian\nID_LIKE=debian\n", encoding="utf-8")
            manager = parent / "apt-get"
            manager.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            manager.chmod(0o755)
            missing_git = parent / "missing-git"
            host = host_requirements.inspect_host(
                system_name="Linux",
                machine="x86_64",
                os_release_path=os_release,
            )
            strategy = host_requirements.git_install_strategy(
                host,
                {"PATH": ""},
                effective_uid=0,
                manager_paths={"apt-get": (manager,)},
            )
            with (
                patch.object(
                    host_requirements,
                    "git_candidates",
                    return_value=[
                        {
                            "path": str(missing_git),
                            "source": "linux-common-directory",
                        }
                    ],
                ),
                patch.object(
                    host_requirements,
                    "git_install_strategy",
                    return_value=strategy,
                ),
            ):
                host_check = host_requirements.inspect_host_requirements(
                    environment={"PATH": ""},
                    system_name="Linux",
                    machine="x86_64",
                    os_release_path=os_release,
                )
            with patch.object(
                repair_cli,
                "inspect_host_requirements",
                return_value=host_check,
            ):
                check = repair_cli.inspect_repair(
                    environment={"PATH": ""},
                    record_path=parent / "setup-v1.json",
                )
            plan = repair_cli.build_repair_plan(check)

            self._validate("check", check)
            self._validate("plan", plan)
            with self.assertRaises(repair_cli.RepairPartialError) as raised:
                repair_cli._apply_plan(
                    plan,
                    environment={"PATH": ""},
                    explicit_git=None,
                    output=io.StringIO(),
                )
            result = raised.exception.result

        self.assertEqual("available", check["requirements"]["git"]["repair"]["state"])
        self.assertEqual(["install-git"], [row["id"] for row in plan["actions"]])
        self.assertEqual("partial", result["outcome"])
        self.assertEqual("failed-exit-7", result["installed"][0]["outcome"])
        self._validate("result", result)

    def test_schemas_reject_extensions_and_inconsistent_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            check, plan, result = self._ready_payloads(Path(temporary))

        extra_check = deepcopy(check)
        extra_check["host"]["unexpected"] = True
        with self.assertRaises(ValidationError):
            self._validate("check", extra_check)

        inconsistent_setup = deepcopy(check)
        inconsistent_setup["setup"]["state"] = "configured"
        with self.assertRaises(ValidationError):
            self._validate("check", inconsistent_setup)

        extra_plan = deepcopy(plan)
        extra_plan["actions"][0]["unexpected"] = True
        with self.assertRaises(ValidationError):
            self._validate("plan", extra_plan)

        inconsistent_plan = deepcopy(plan)
        inconsistent_plan["state"] = "blocked"
        with self.assertRaises(ValidationError):
            self._validate("plan", inconsistent_plan)

        extra_result = deepcopy(result)
        extra_result["git"]["unexpected"] = True
        with self.assertRaises(ValidationError):
            self._validate("result", extra_result)

        inconsistent_result = deepcopy(result)
        inconsistent_result["outcome"] = "partial"
        with self.assertRaises(ValidationError):
            self._validate("result", inconsistent_result)


if __name__ == "__main__":
    unittest.main()
