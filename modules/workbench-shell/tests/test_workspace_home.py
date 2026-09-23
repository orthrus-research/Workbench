"""Executable product tests for the developer-first workspace Home."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = REPOSITORY_ROOT / "modules/project-intelligence/src"
ATLAS_ROOT = REPOSITORY_ROOT / "modules/atlas/src"
BLUEPRINTS_ROOT = REPOSITORY_ROOT / "modules/blueprints/src"
sys.path.insert(0, str(ATLAS_ROOT))
sys.path.insert(0, str(BLUEPRINTS_ROOT))
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.workspace_home import (  # noqa: E402
    WorkspaceHomeError,
    build_workspace_home,
    validate_workspace_home,
)


WORKBENCH = REPOSITORY_ROOT / "tools/workbench.py"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


def _snapshot_all(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _cleanroom_mod(parent: Path) -> Path:
    project = parent / "example-mod"
    _write(project / "settings.gradle", "rootProject.name = 'example-mod'\n")
    _write(
        project / "build.gradle",
        """\
plugins {
    id 'java'
    id 'com.cleanroommc.gradle' version '0.3.1'
}
dependencies {
    minecraft 'com.cleanroommc:cleanroom:0.3-alpha'
}
sourceCompatibility = JavaVersion.VERSION_1_8
targetCompatibility = JavaVersion.VERSION_1_8
""",
    )
    _write(
        project / "gradle.properties",
        "minecraft_version=1.12.2\ncleanroom_version=0.3-alpha\n",
    )
    _write(
        project / "gradle/wrapper/gradle-wrapper.properties",
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.7-bin.zip\n",
    )
    _write(project / "gradlew", "#!/bin/sh\nexit 0\n")
    (project / "gradlew").chmod(0o755)
    _write(
        project / "src/main/resources/mcmod.info",
        json.dumps([{"modid": "examplemod", "name": "Example Mod", "version": "1"}]),
    )
    _write(project / "src/main/java/example/Example.java", "package example;\n")
    return project


def _supersymmetry_pack(
    parent: Path,
    *,
    declared_hash: str = EMPTY_SHA256,
    feature_ready: bool = False,
    loader_id: str = "forge",
    loader_version: str = "14.23.5.2860",
    pack_format: str = "packwiz:1.1.0",
    initialize_git: bool = True,
) -> Path:
    project = parent / "supersymmetry"
    project.mkdir()
    for directory in ("config", "groovy", "mods"):
        (project / directory).mkdir()
    _write(
        project / "pack.toml",
        f"""\
name = "Supersymmetry"
author = "SymmetricDevs"
version = "test"
pack-format = "{pack_format}"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "{declared_hash}"

[versions]
{loader_id} = "{loader_version}"
minecraft = "1.12.2"
""",
    )
    _write(project / "index.toml", "")
    if feature_ready:
        _write(
            project / "groovy/prePostInit/Recipemaps.groovy",
            "package prePostInit\n\n"
            "class Recipemaps {\n"
            "    static final def MIXER = recipemap('mixer')\n"
            "}\n",
        )
        _write(
            project / "groovy/postInit/chemistry/Probe.groovy",
            "import static prePostInit.Recipemaps.*\n\n"
            "MIXER.recipeBuilder().duration(20).buildAndRegister()\n",
        )
    if initialize_git:
        _git(project, "init", "--quiet")
        _git(project, "config", "user.name", "Workbench Test")
        _git(project, "config", "user.email", "workbench@example.invalid")
        _git(project, "add", ".")
        _git(project, "commit", "--quiet", "-m", "fixture")
    return project


class WorkspaceHomeTests(unittest.TestCase):
    def test_recognizes_nested_cleanroom_mod_and_returns_exact_next_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _cleanroom_mod(Path(temporary))
            nested = project / "src/main/java/example"
            before = _snapshot(project)

            home = build_workspace_home(REPOSITORY_ROOT, nested)

            self.assertEqual("cleanroom-mod", home["workspace"]["kind"])
            self.assertEqual(str(project), home["workspace"]["root"])
            self.assertEqual("Example Mod", home["workspace"]["display_name"])
            self.assertEqual("cleanroom", home["context"]["platform"]["kind"])
            self.assertEqual("1.12.2", home["context"]["platform"]["minecraft_version"])
            self.assertEqual("0.3-alpha", home["context"]["platform"]["cleanroom_version"])
            self.assertEqual("8.7", home["context"]["build"]["wrapper"]["version"])
            self.assertEqual("ready", home["status"]["status"])
            self.assertLessEqual(len(home["actions"]), 5)
            self.assertEqual(
                ["workbench", "doctor", str(project)],
                next(
                    row for row in home["actions"] if row["id"] == "workspace-health"
                )["argv"],
            )
            blocked = {row["id"]: row for row in home["actions"] if not row["available"]}
            self.assertEqual(
                ["EXACT_RUN_PROFILE_REQUIRED"],
                blocked["run-development-client"]["blockers"],
            )
            for action in home["actions"]:
                if not action["available"]:
                    continue
                result = subprocess.run(
                    [sys.executable, str(WORKBENCH), *action["argv"][1:]],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    f"{action['id']}: {result.stderr or result.stdout}",
                )
            self.assertEqual(before, _snapshot(project))

    def test_unknown_directory_is_useful_without_inventing_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "notes"
            project.mkdir()
            _write(project / "README.md", "not a Minecraft project\n")
            before = _snapshot(project)

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("directory", home["workspace"]["kind"])
            self.assertEqual("unresolved", home["context"]["profile"]["state"])
            self.assertIsNone(home["context"]["profile"]["profile_family_id"])
            self.assertNotIn("Supersymmetry", json.dumps(home))
            self.assertTrue(next(row for row in home["actions"] if row["id"] == "workspace-health")["available"])
            search = next(
                row for row in home["actions"] if row["id"] == "search-workspace"
            )
            self.assertFalse(search["available"])
            self.assertEqual(["PROJECT_SURFACE_UNAVAILABLE"], search["blockers"])
            self.assertEqual(before, _snapshot(project))

    def test_exact_supersymmetry_pack_exposes_problem_flows_not_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(Path(temporary), feature_ready=True)
            marker = project / "OPEN_MUTATED_SOURCE"
            hook = project / ".git/fsmonitor-audit.sh"
            _write(
                hook,
                f"#!/bin/sh\nprintf executed >> '{marker}'\nprintf 'token\\n'\n",
            )
            hook.chmod(0o755)
            _git(project, "config", "core.fsmonitor", str(hook))
            before = _snapshot(project)

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("supersymmetry-pack", home["workspace"]["kind"])
            self.assertEqual("exact", home["workspace"]["recognition"])
            self.assertEqual("Supersymmetry", home["workspace"]["display_name"])
            profile = home["context"]["profile"]
            self.assertEqual("workbench-pack:supersymmetry", profile["profile_family_id"])
            self.assertEqual("cleanroom-provisional", profile["selected_profile"])
            self.assertEqual("experimental", profile["maturity"])
            self.assertFalse(profile["support_claimed"])
            self.assertEqual(
                [{"id": "forge", "version": "14.23.5.2860"}],
                home["context"]["pack_loader"]["items"],
            )
            actions = {row["id"]: row for row in home["actions"]}
            self.assertEqual(
                ["workbench", "feature", "options", "recipe-change", str(project)],
                actions["change-recipe"]["argv"],
            )
            self.assertEqual(
                ["workbench", "atlas", "recipes", "context", str(project)],
                actions["understand-recipes"]["argv"],
            )
            self.assertTrue(all(row["available"] for row in home["actions"]))
            for action in home["actions"]:
                result = subprocess.run(
                    [sys.executable, str(WORKBENCH), *action["argv"][1:]],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    f"{action['id']}: {result.stderr or result.stdout}",
                )
            self.assertFalse(marker.exists())
            self.assertEqual(before, _snapshot(project))

    def test_open_disables_repository_code_and_git_index_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _cleanroom_mod(Path(temporary))
            _write(project / ".gitattributes", "*.txt filter=audit\n")
            _write(project / "payload.txt", "unchanged bytes\n")
            _git(project, "init", "--quiet")
            _git(project, "config", "user.name", "Workbench Test")
            _git(project, "config", "user.email", "workbench@example.invalid")
            _git(project, "add", ".")
            _git(project, "commit", "--quiet", "-m", "fixture")
            marker = project / "OPEN_MUTATED_SOURCE"
            hook = project / ".git/fsmonitor-audit.sh"
            _write(
                hook,
                f"#!/bin/sh\nprintf executed >> '{marker}'\nprintf 'token\\n'\n",
            )
            hook.chmod(0o755)
            _git(project, "config", "core.fsmonitor", str(hook))
            clean_filter = project / ".git/clean-filter-audit.sh"
            _write(
                clean_filter,
                f"#!/bin/sh\nprintf filter >> '{marker}'\ncat\n",
            )
            clean_filter.chmod(0o755)
            _git(project, "config", "filter.audit.clean", str(clean_filter))
            payload = project / "payload.txt"
            metadata = payload.stat()
            os.utime(payload, (metadata.st_atime, metadata.st_mtime + 5))
            before = _snapshot_all(project)

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("cleanroom-mod", home["workspace"]["kind"])
            self.assertFalse(marker.exists())
            self.assertEqual(before, _snapshot_all(project))

    def test_exact_pack_only_advertises_recipe_flows_after_owner_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(Path(temporary))

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("exact", home["workspace"]["recognition"])
            actions = {row["id"]: row for row in home["actions"]}
            self.assertFalse(actions["change-recipe"]["available"])
            self.assertEqual(
                ["RECIPE_OWNER_PREFLIGHT_FAILED"],
                actions["change-recipe"]["blockers"],
            )
            self.assertIn("groovy/postInit", actions["change-recipe"]["unavailable_reason"])
            self.assertFalse(actions["understand-recipes"]["available"])
            self.assertEqual(
                ["RECIPE_EVIDENCE_PREFLIGHT_FAILED"],
                actions["understand-recipes"]["blockers"],
            )
            self.assertFalse(actions["search-workspace"]["available"])
            self.assertEqual(
                ["PROJECT_SURFACE_UNAVAILABLE"],
                actions["search-workspace"]["blockers"],
            )

    def test_non_forge_pack_loader_cannot_select_the_cleanroom_target_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(
                Path(temporary),
                loader_id="fabric-loader",
                loader_version="0.14.0",
            )

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("bounded", home["workspace"]["recognition"])
            self.assertEqual("unresolved", home["context"]["profile"]["state"])
            failure = next(
                row
                for row in home["problems"]
                if row["id"] == "EXACT_PACK_CONTEXT_UNAVAILABLE"
            )
            self.assertIn("does not match", failure["detail"])
            self.assertIn("fabric-loader", failure["detail"])

    def test_wrong_supersymmetry_loader_version_and_pack_format_are_not_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wrong_loader = _supersymmetry_pack(
                Path(temporary), loader_version="999"
            )
            loader_home = build_workspace_home(REPOSITORY_ROOT, wrong_loader)
            self.assertEqual("bounded", loader_home["workspace"]["recognition"])
            loader_failure = next(
                row
                for row in loader_home["problems"]
                if row["id"] == "EXACT_PACK_CONTEXT_UNAVAILABLE"
            )
            self.assertIn("does not match", loader_failure["detail"])

        with tempfile.TemporaryDirectory() as temporary:
            wrong_format = _supersymmetry_pack(
                Path(temporary), pack_format="not-packwiz"
            )
            format_home = build_workspace_home(REPOSITORY_ROOT, wrong_format)
            self.assertNotEqual("exact", format_home["workspace"]["recognition"])
            self.assertTrue(
                any(
                    row["id"] == "PACK_MANIFEST_INVALID"
                    for row in format_home["problems"]
                )
            )

    def test_generic_packwiz_pack_preserves_manifest_identity_without_profile_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(Path(temporary))
            manifest = (project / "pack.toml").read_text(encoding="utf-8")
            (project / "pack.toml").write_text(
                manifest.replace('name = "Supersymmetry"', 'name = "Other Pack"'),
                encoding="utf-8",
            )

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("packwiz-pack", home["workspace"]["kind"])
            self.assertEqual("Other Pack", home["workspace"]["display_name"])
            self.assertEqual("bounded", home["workspace"]["recognition"])
            self.assertEqual("unresolved", home["context"]["profile"]["state"])
            self.assertEqual("observed", home["context"]["pack_loader"]["state"])
            self.assertEqual(
                [{"id": "forge", "version": "14.23.5.2860"}],
                home["context"]["pack_loader"]["items"],
            )
            problem_ids = {row["id"] for row in home["problems"]}
            self.assertIn("PACK_PROFILE_UNRESOLVED", problem_ids)
            self.assertNotIn("BUILD_PROVIDER_UNRESOLVED", problem_ids)
            self.assertNotIn("PLATFORM_UNRESOLVED", problem_ids)

    def test_generic_packwiz_requires_a_verified_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(Path(temporary))
            manifest = (project / "pack.toml").read_text(encoding="utf-8")
            (project / "pack.toml").write_text(
                manifest.replace('name = "Supersymmetry"', 'name = "Other Pack"'),
                encoding="utf-8",
            )
            _write(project / "index.toml", "changed\n")

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("packwiz-pack", home["workspace"]["kind"])
            self.assertEqual("bounded", home["workspace"]["recognition"])
            issue = next(
                row for row in home["problems"] if row["id"] == "PACK_INDEX_INVALID"
            )
            self.assertIn("does not match", issue["detail"])
            self.assertFalse(
                home["context"]["pack_manifest"]["index"][
                    "matches_declared_hash"
                ]
            )

        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "fake"
            fake.mkdir()
            _write(
                fake / "pack.toml",
                'name="Not Packwiz"\npack-format="packwiz:1.1.0"\n'
                '[versions]\nminecraft="1.12.2"\nforge="bogus"\n',
            )
            home = build_workspace_home(REPOSITORY_ROOT, fake)
            self.assertNotEqual("packwiz-pack", home["workspace"]["kind"])
            self.assertTrue(
                any(
                    row["id"] == "PACK_MANIFEST_INVALID"
                    for row in home["problems"]
                )
            )

    def test_nested_packwiz_checkout_wins_over_monorepo_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            monorepo = Path(temporary) / "monorepo"
            monorepo.mkdir()
            packs = monorepo / "packs"
            packs.mkdir()
            project = _supersymmetry_pack(packs, initialize_git=False)
            _git(monorepo, "init", "--quiet")
            _git(monorepo, "config", "user.name", "Workbench Test")
            _git(monorepo, "config", "user.email", "workbench@example.invalid")
            _git(monorepo, "add", ".")
            _git(monorepo, "commit", "--quiet", "-m", "fixture")

            home = build_workspace_home(REPOSITORY_ROOT, project / "config")

            self.assertEqual(str(project), home["workspace"]["root"])
            self.assertEqual("supersymmetry-pack", home["workspace"]["kind"])
            self.assertEqual("exact", home["workspace"]["recognition"])

    def test_malformed_pack_manifest_and_mod_descriptor_affect_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            malformed_pack = Path(temporary) / "broken-pack"
            malformed_pack.mkdir()
            _write(malformed_pack / "pack.toml", 'name = "Supersymmetry"\n[index\n')

            pack_home = build_workspace_home(REPOSITORY_ROOT, malformed_pack)

            pack_problem = next(
                row
                for row in pack_home["problems"]
                if row["id"] == "PACK_MANIFEST_INVALID"
            )
            self.assertIn("malformed", pack_problem["detail"])
            self.assertEqual(
                sum(row["severity"] == "warning" for row in pack_home["problems"]),
                pack_home["status"]["warnings"],
            )
            self.assertEqual("attention", pack_home["status"]["status"])

            mod = _cleanroom_mod(Path(temporary))
            _write(mod / "src/main/resources/mcmod.info", "[{ definitely not json ]")

            mod_home = build_workspace_home(REPOSITORY_ROOT, mod)

            self.assertTrue(
                any(
                    row["id"].startswith("MOD_DESCRIPTOR_INVALID:")
                    for row in mod_home["problems"]
                )
            )
            self.assertEqual("attention", mod_home["status"]["status"])
            self.assertGreaterEqual(mod_home["status"]["warnings"], 1)

    def test_stale_supersymmetry_index_has_non_mutating_provisioning_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(Path(temporary), declared_hash="0" * 64)
            before = _snapshot(project)

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertEqual("bounded", home["workspace"]["recognition"])
            failure = next(
                row
                for row in home["problems"]
                if row["id"] == "PACK_INDEX_REFRESHABLE"
            )
            self.assertIn("does not match", failure["detail"])
            self.assertEqual(
                f"workbench runtime preflight {project} --profile supersymmetry",
                failure["repair"]["command"],
            )
            self.assertFalse(
                any(
                    row["id"] == "EXACT_PACK_CONTEXT_UNAVAILABLE"
                    for row in home["problems"]
                )
            )
            action = next(
                row
                for row in home["actions"]
                if row["id"] == "prepare-supersymmetry-runtime"
            )
            self.assertTrue(action["available"])
            self.assertEqual(
                [
                    "workbench",
                    "runtime",
                    "preflight",
                    str(project),
                    "--profile",
                    "supersymmetry",
                ],
                action["argv"],
            )
            self.assertEqual(before, _snapshot(project))

    def test_pack_name_alone_does_not_enable_supersymmetry_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _supersymmetry_pack(Path(temporary), declared_hash="0" * 64)
            (project / "groovy").rmdir()

            home = build_workspace_home(REPOSITORY_ROOT, project)

            self.assertTrue(
                any(row["id"] == "PACK_INDEX_INVALID" for row in home["problems"])
            )
            self.assertTrue(
                any(
                    row["id"] == "EXACT_PACK_CONTEXT_UNAVAILABLE"
                    for row in home["problems"]
                )
            )
            self.assertFalse(
                any(
                    row["id"] == "prepare-supersymmetry-runtime"
                    for row in home["actions"]
                )
            )

    def test_validator_rejects_fake_available_action_without_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = build_workspace_home(REPOSITORY_ROOT, Path(temporary))
            available = next(row for row in home["actions"] if row["available"])
            available["argv"] = None
            with self.assertRaisesRegex(WorkspaceHomeError, "lacks exact argv"):
                validate_workspace_home(home)

    def test_public_router_supports_open_and_bare_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _cleanroom_mod(Path(temporary))
            environment = os.environ.copy()
            environment["WORKBENCH_CONFIG_HOME"] = str(
                Path(temporary) / "isolated-config"
            )
            environment.pop("WORKBENCH_WORKSPACE", None)
            opened = subprocess.run(
                [sys.executable, str(WORKBENCH), "open", str(project), "--json"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, opened.returncode, opened.stderr)
            self.assertEqual("cleanroom-mod", json.loads(opened.stdout)["workspace"]["kind"])

            bare = subprocess.run(
                [sys.executable, str(WORKBENCH)],
                cwd=project,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, bare.returncode, bare.stderr)
            self.assertIn("Workbench Core:", bare.stdout)
            self.assertNotIn("Workspace: Example Mod", bare.stdout)

    def test_missing_path_is_a_real_cli_failure(self) -> None:
        missing = REPOSITORY_ROOT / ".workbench-does-not-exist"
        result = subprocess.run(
            [sys.executable, str(WORKBENCH), "open", str(missing), "--json"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("Workbench open failed:", result.stderr)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
