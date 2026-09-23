from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence.workspace_doctor import (  # noqa: E402
    WorkspaceDoctorError,
    discover_workspace_root,
    new_report,
    render_workspace_doctor_report,
    validate_workspace_doctor_report,
)
import workbench_project_intelligence.workspace_doctor as doctor_module  # noqa: E402


SCHEMA = json.loads(
    (
        ROOT
        / "modules/project-intelligence/schemas/"
        "workspace-doctor-report-v1.schema.json"
    ).read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)
TOOL = ROOT / "tools/workbench.py"


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _cleanroom_fixture(base: Path) -> tuple[Path, Path, Path]:
    repository = base / "repository"
    project = repository / "mods/example"
    requested = project / "src/main/java/example"

    _write(repository / "settings.gradle", "rootProject.name = 'outer'\n")
    _write(project / "settings.gradle", "rootProject.name = 'example'\n")
    _write(
        project / "build.gradle",
        """plugins {
    id 'java'
    id 'groovy'
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(17)
    }
}
sourceCompatibility = JavaVersion.VERSION_1_8
targetCompatibility = JavaVersion.VERSION_1_8

repositories {
    maven { url = 'https://maven.cleanroommc.com' }
}
dependencies {
    implementation 'com.cleanroommc:cleanroom:0.3.0-alpha'
}
""",
    )
    _write(
        project / "gradle.properties",
        """minecraft_version=1.12.2
cleanroom_version=0.3.0-alpha
mcp_channel=stable
mcp_version=39-1.12
""",
    )
    _write(project / "gradlew", "#!/bin/sh\nexit 0\n", executable=True)
    _write(project / "gradlew.bat", "@echo off\r\nexit /b 0\r\n")
    _write(
        project / "gradle/wrapper/gradle-wrapper.properties",
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
    )
    _write(
        requested / "ExampleMod.java",
        "package example;\npublic final class ExampleMod {}\n",
    )
    _write(
        project / "src/main/groovy/example/Recipes.groovy",
        "package example\n",
    )
    _write(
        project / "src/main/resources/mcmod.info",
        json.dumps(
            [
                {
                    "modid": "example",
                    "name": "Example Mod",
                    "version": "1.0.0",
                }
            ]
        )
        + "\n",
    )
    _write(project / "src/main/resources/mixins.example.json", "{}\n")
    _write(project / "scripts/recipes/example.groovy", "// pack script\n")
    _write(project / "config/example.cfg", "enabled=true\n")
    return repository, project, requested


def _snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            snapshot[relative] = ("symlink", mode, os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", mode)
        else:
            snapshot[relative] = ("file", mode, path.read_bytes())
    return snapshot


def _assert_schema(test: unittest.TestCase, report: dict[str, object]) -> None:
    errors = sorted(VALIDATOR.iter_errors(report), key=lambda error: list(error.path))
    test.assertEqual([], [(list(error.path), error.message) for error in errors])


class WorkspaceDoctorTests(unittest.TestCase):
    def test_discovers_nearest_cleanroom_gradle_root_and_project_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, project, requested = _cleanroom_fixture(Path(temporary))
            canonical_project = project.resolve()
            before = _snapshot(repository)

            self.assertEqual(canonical_project, discover_workspace_root(requested))
            first = new_report(requested, requested_path=requested)
            second = new_report(requested, requested_path=requested)

            self.assertEqual(first, second)
            validate_workspace_doctor_report(first)
            _assert_schema(self, first)
            self.assertEqual(
                {
                    "state": "observed",
                    "requested_path": str(requested),
                    "root": str(canonical_project),
                    "kind": "gradle-project",
                },
                first["target"]["workspace"],
            )
            self.assertEqual(
                {
                    "state": "declared",
                    "kind": "cleanroom",
                    "minecraft_version": "1.12.2",
                    "cleanroom_version": "0.3.0-alpha",
                    "forge_version": None,
                    "mappings": {"channel": "stable", "version": "39-1.12"},
                    "maturity": None,
                    "profile_id": None,
                    "evidence": [
                        "gradle.properties",
                        "settings.gradle",
                        "build.gradle",
                    ],
                },
                first["target"]["platform"],
            )
            build = first["target"]["build"]
            self.assertEqual("gradle", build["provider"])
            self.assertEqual(["settings.gradle", "build.gradle"], build["scripts"])
            self.assertEqual(["groovy", "java"], build["plugins"])
            self.assertEqual(
                ["com.cleanroommc:cleanroom:0.3.0-alpha"],
                build["dependency_coordinates"],
            )
            self.assertEqual(
                {
                    "toolchain_language": "17",
                    "source_compatibility": "1.8",
                    "target_compatibility": "1.8",
                },
                build["declared_java"],
            )
            self.assertEqual("8.7", build["wrapper"]["version"])
            self.assertTrue(build["wrapper"]["executable"])

            surfaces = first["target"]["surfaces"]
            self.assertEqual(
                ["src/main/groovy", "src/main/java"], surfaces["source"]
            )
            self.assertEqual(["src/main/resources"], surfaces["resources"])
            self.assertEqual(
                ["scripts", "src/main/groovy"], surfaces["groovy"]
            )
            self.assertEqual(["config"], surfaces["configuration"])
            self.assertEqual(
                ["src/main/resources/mixins.example.json"],
                surfaces["mixin_configs"],
            )
            self.assertEqual(
                [
                    {
                        "mod_id": "example",
                        "name": "Example Mod",
                        "version": "1.0.0",
                        "descriptor": str(
                            canonical_project / "src/main/resources/mcmod.info"
                        ),
                    }
                ],
                surfaces["mods"],
            )
            self.assertEqual(
                {
                    "status": "ready",
                    "blockers": 0,
                    "warnings": 0,
                    "information": 0,
                },
                first["summary"],
            )
            self.assertEqual([], first["findings"])

            expected_render = (
                "Workbench doctor\n"
                "Status: ready (0 blockers, 0 warnings, 0 info)\n"
                "Capability: workspace-context\n"
                f"Requested context: {requested}\n"
                f"Workspace: {canonical_project} [gradle-project]\n"
                "Profile: unresolved (none)\n"
                "Platform: cleanroom / 1.12.2 / 0.3.0-alpha [declared]\n"
                "Mappings: stable / 39-1.12\n"
                "Build: gradle [observed]\n"
                "Runtime: runtime inspection requires an applicable capability profile [unavailable]\n"
                "Findings: none in the bounded V1 checks\n"
                "No project files, builds, runtimes, or worlds were changed.\n"
            )
            self.assertEqual(expected_render, render_workspace_doctor_report(first))
            self.assertEqual(
                render_workspace_doctor_report(first),
                render_workspace_doctor_report(second),
            )
            self.assertEqual(before, _snapshot(repository))

    def test_unresolved_platform_is_an_explicit_ordered_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "plain-gradle"
            _write(workspace / "settings.gradle", "rootProject.name = 'plain'\n")
            _write(workspace / "build.gradle", "plugins { id 'java' }\n")

            report = new_report(workspace)

            validate_workspace_doctor_report(report)
            _assert_schema(self, report)
            self.assertEqual("unresolved", report["target"]["platform"]["state"])
            self.assertEqual(
                {
                    "status": "attention",
                    "blockers": 0,
                    "warnings": 1,
                    "information": 0,
                },
                report["summary"],
            )
            self.assertEqual(["PLATFORM_UNRESOLVED"], report["repair_plan"])
            self.assertEqual(
                ["PLATFORM_UNRESOLVED"],
                [finding["id"] for finding in report["findings"]],
            )
            self.assertFalse(report["findings"][0]["repair"]["mutates"])

    def test_cleanroom_repository_alone_does_not_reclassify_legacy_forge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "legacy-forge"
            _write(workspace / "settings.gradle", "rootProject.name = 'legacy'\n")
            _write(
                workspace / "build.gradle",
                """plugins {
    id 'net.minecraftforge.gradle.forge'
}
repositories {
    maven { url = 'https://maven.cleanroommc.com' }
}
""",
            )
            _write(
                workspace / "gradle.properties",
                """minecraft_version=1.12.2
forge_version=14.23.5.2860
mcp_channel=stable
mcp_version=39-1.12
""",
            )

            report = new_report(workspace)

            validate_workspace_doctor_report(report)
            _assert_schema(self, report)
            platform = report["target"]["platform"]
            self.assertEqual("declared", platform["state"])
            self.assertEqual("legacy-forge", platform["kind"])
            self.assertEqual("14.23.5.2860", platform["forge_version"])
            self.assertIsNone(platform["cleanroom_version"])
            self.assertEqual(
                ["LEGACY_FORGE_OBSERVATION_ONLY"],
                [finding["id"] for finding in report["findings"]],
            )
            self.assertEqual("attention", report["summary"]["status"])

    def test_failed_git_status_probe_is_unavailable_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, _, requested = _cleanroom_fixture(Path(temporary))

            def probe(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str] | None:
                if "--show-toplevel" in command:
                    return subprocess.CompletedProcess(
                        command, 0, stdout=f"{repository}\n", stderr=""
                    )
                if command[-2:] == ["rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(
                        command, 0, stdout=f"{'a' * 40}\n", stderr=""
                    )
                if "symbolic-ref" in command:
                    return subprocess.CompletedProcess(
                        command, 0, stdout="main\n", stderr=""
                    )
                if "status" in command:
                    return None
                self.fail(f"unexpected Git probe: {command}")

            with (
                patch.object(
                    doctor_module,
                    "configured_git_executable",
                    return_value="/mock/git",
                ),
                patch.object(doctor_module, "_run", side_effect=probe),
            ):
                report = new_report(requested)

            validate_workspace_doctor_report(report)
            _assert_schema(self, report)
            context = report["target"]["repository"]
            self.assertEqual("unavailable", context["state"])
            self.assertIsNone(context["dirty"])
            self.assertTrue(context["reason"])
            warnings = [
                finding
                for finding in report["findings"]
                if finding["severity"] == "warning"
            ]
            self.assertEqual(1, len(warnings))
            self.assertIn("git", json.dumps(warnings[0]).lower())
            self.assertEqual([warnings[0]["id"]], report["repair_plan"])
            self.assertEqual("attention", report["summary"]["status"])

    def test_timed_out_git_root_probe_is_unavailable_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, _, requested = _cleanroom_fixture(Path(temporary))
            with (
                patch.object(
                    doctor_module,
                    "configured_git_executable",
                    return_value="/mock/git",
                ),
                patch.object(doctor_module, "_run", return_value=None),
            ):
                report = new_report(requested)

            validate_workspace_doctor_report(report)
            _assert_schema(self, report)
            context = report["target"]["repository"]
            self.assertEqual("unavailable", context["state"])
            self.assertIsNone(context["dirty"])
            warnings = [
                finding
                for finding in report["findings"]
                if finding["severity"] == "warning"
            ]
            self.assertEqual(1, len(warnings))
            self.assertIn("git", json.dumps(warnings[0]).lower())
            self.assertEqual("attention", report["summary"]["status"])

    def test_semantic_validator_rejects_mutation_and_inconsistent_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "plain-gradle"
            _write(workspace / "settings.gradle", "rootProject.name = 'plain'\n")
            report = new_report(workspace)

            variants = []
            writable = deepcopy(report)
            writable["read_only"] = False
            variants.append((writable, "must be read-only"))
            mutating_repair = deepcopy(report)
            mutating_repair["findings"][0]["repair"]["mutates"] = True
            variants.append((mutating_repair, "non-applied proposals"))
            inconsistent = deepcopy(report)
            inconsistent["summary"]["warnings"] = 0
            variants.append((inconsistent, "does not match findings"))

            for invalid, message in variants:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(WorkspaceDoctorError, message):
                        validate_workspace_doctor_report(invalid)

    @unittest.skipUnless(shutil.which("git"), "git is required for worktree context")
    def test_reports_dirty_git_context_without_changing_the_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, project, requested = _cleanroom_fixture(Path(temporary))
            git = shutil.which("git")
            assert git is not None
            subprocess.run(
                [git, "init", "-q", str(repository)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [git, "-C", str(repository), "add", "."],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    git,
                    "-c",
                    "user.name=Workbench Test",
                    "-c",
                    "user.email=workbench@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "-C",
                    str(repository),
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            with (project / "build.gradle").open("a", encoding="utf-8") as stream:
                stream.write("// local edit\n")
            _write(project / "untracked.txt", "untracked\n")
            before = _snapshot(repository)

            report = new_report(requested)

            repository_context = report["target"]["repository"]
            self.assertEqual("observed", repository_context["state"])
            self.assertEqual(str(repository.resolve()), repository_context["root"])
            self.assertEqual("mods/example", repository_context["workspace_pathspec"])
            self.assertTrue(repository_context["dirty"])
            self.assertEqual(
                {"unstaged": 1, "untracked": 1}, repository_context["changes"]
            )
            self.assertEqual(40, len(repository_context["head"]))
            self.assertIn(
                "WORKTREE_DIRTY", [finding["id"] for finding in report["findings"]]
            )
            self.assertEqual("ready", report["summary"]["status"])
            self.assertEqual(1, report["summary"]["information"])
            _assert_schema(self, report)
            self.assertEqual(before, _snapshot(repository))

    def test_workspace_context_cli_emits_deterministic_schema_valid_json_read_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, _, requested = _cleanroom_fixture(Path(temporary))
            before = _snapshot(repository)
            command = [
                sys.executable,
                str(TOOL),
                "doctor",
                str(requested),
                "--capability",
                "workspace-context",
                "--json",
            ]

            first = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            second = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual("", first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            report = json.loads(first.stdout)
            validate_workspace_doctor_report(report)
            _assert_schema(self, report)
            self.assertEqual("workspace-context", report["capability"])
            self.assertTrue(report["read_only"])
            self.assertEqual(before, _snapshot(repository))

    def test_workspace_context_cli_refuses_to_overwrite_an_existing_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            _, _, requested = _cleanroom_fixture(base)
            output = base / "doctor-report.json"
            original = b"user-owned report\n"
            output.write_bytes(original)
            before = _snapshot(base)

            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "doctor",
                    str(requested),
                    "--capability",
                    "workspace-context",
                    "--output",
                    str(output),
                    "--json",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertIn("doctor output already exists", result.stderr)
            self.assertIn(str(output.resolve()), result.stderr)
            self.assertEqual(original, output.read_bytes())
            self.assertEqual(before, _snapshot(base))


if __name__ == "__main__":
    unittest.main()
