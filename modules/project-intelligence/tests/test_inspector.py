"""Focused tests for the read-only workspace inspector."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_project_intelligence import (  # noqa: E402
    ProjectInspectionError,
    inspect_workspace,
)


PLATFORM_YAML = """\
schema_version: 1
profile_id: workbench-platform:cleanroom:test
kind: cleanroom
status: provisional
minecraft_version: 1.12.2
cleanroom_version: unresolved
"""

PACK_YAML = """\
schema_version: 1
profile_family_id: workbench-pack:test
display_name: Test Pack
status: active-bootstrap
workspace:
  kind: packwiz-modpack
  expected_name: Test Pack
  required_paths:
    - path: pack.toml
      kind: file
    - path: index.toml
      kind: file
    - path: config
      kind: directory
    - path: groovy
      kind: directory
    - path: mods
      kind: directory
profiles:
  cleanroom-test:
    platform_profile_id: workbench-platform:cleanroom:test
    maturity: experimental
    permitted_operations:
      - observe
      - construct
"""

PACK_TOML = """\
name = "Test Pack"
author = "Workbench Test"
version = "0.0.1"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

[versions]
forge = "14.23.5.2860"
minecraft = "1.12.2"
"""


def _run(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class InspectorTest(unittest.TestCase):
    def _workspace(
        self,
        parent: Path,
        *,
        include_profiles: bool = True,
    ) -> Path:
        workspace = parent / "workspace"
        workspace.mkdir()
        for directory in ("config", "groovy", "mods"):
            (workspace / directory).mkdir()
        (workspace / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
        (workspace / "index.toml").write_text("", encoding="utf-8")
        if include_profiles:
            (workspace / "profiles/platforms/cleanroom").mkdir(parents=True)
            (workspace / "profiles/packs/supersymmetry").mkdir(parents=True)
            (
                workspace
                / "profiles/platforms/cleanroom/provisional.yaml"
            ).write_text(
                PLATFORM_YAML,
                encoding="utf-8",
            )
            (
                workspace
                / "profiles/packs/supersymmetry/profile.yaml"
            ).write_text(
                PACK_YAML,
                encoding="utf-8",
            )
        _run(workspace, "init", "--quiet")
        _run(workspace, "config", "user.name", "Workbench Test")
        _run(workspace, "config", "user.email", "workbench@example.invalid")
        _run(workspace, "add", ".")
        _run(workspace, "commit", "--quiet", "-m", "fixture")
        return workspace

    @staticmethod
    def _inspect(workspace: Path) -> dict[str, object]:
        return inspect_workspace(
            workspace,
            platform_profile_path=(
                workspace / "profiles/platforms/cleanroom/provisional.yaml"
            ),
            pack_profile_path=(
                workspace / "profiles/packs/supersymmetry/profile.yaml"
            ),
            pack_selection="cleanroom-test",
        )

    def test_inspection_binds_git_platform_and_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            result = self._inspect(workspace)

            self.assertEqual(result["schema_version"], 2)
            self.assertFalse(result["workspace"]["dirty"])
            self.assertEqual(
                result["platform"]["profile_id"],
                "workbench-platform:cleanroom:test",
            )
            self.assertEqual(
                result["pack"]["platform_profile_id"],
                result["platform"]["profile_id"],
            )
            self.assertEqual(result["project"]["name"], "Test Pack")
            self.assertEqual(
                result["project"]["loaders"],
                [{"id": "forge", "version": "14.23.5.2860"}],
            )
            self.assertTrue(
                result["project"]["index"]["matches_declared_hash"]
            )
            self.assertEqual(
                len(result["platform"]["document_sha256"]),
                64,
            )

            schema = json.loads(
                (
                    MODULE_ROOT
                    / "schemas/workspace-context-v2.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(result)

            (workspace / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            dirty = self._inspect(workspace)
            self.assertTrue(dirty["workspace"]["dirty"])
            self.assertIn("?? untracked.txt", dirty["workspace"]["dirty_entries"])

    def test_profiles_can_live_outside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = self._workspace(parent, include_profiles=False)
            authority = parent / "authority"
            authority.mkdir()
            platform_path = authority / "platform.yaml"
            pack_path = authority / "pack.yaml"
            platform_path.write_text(PLATFORM_YAML, encoding="utf-8")
            pack_path.write_text(PACK_YAML, encoding="utf-8")

            result = inspect_workspace(
                workspace,
                platform_profile_path=platform_path,
                pack_profile_path=pack_path,
                pack_selection="cleanroom-test",
            )

            self.assertEqual(result["project"]["name"], "Test Pack")
            self.assertFalse((workspace / "profiles").exists())

    def test_inspection_can_use_one_preloaded_profile_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = self._workspace(parent, include_profiles=False)
            platform_path = parent / "platform.yaml"
            pack_path = parent / "pack.yaml"
            platform_bytes = PLATFORM_YAML.encode("utf-8")
            pack_bytes = PACK_YAML.encode("utf-8")

            result = inspect_workspace(
                workspace,
                platform_profile_path=platform_path,
                pack_profile_path=pack_path,
                pack_selection="cleanroom-test",
                platform_profile_bytes=platform_bytes,
                pack_profile_bytes=pack_bytes,
            )

            self.assertEqual(result["project"]["name"], "Test Pack")
            self.assertEqual(
                result["platform"]["document_sha256"],
                sha256(platform_bytes).hexdigest(),
            )
            self.assertFalse(platform_path.exists())
            self.assertFalse(pack_path.exists())

    def test_pack_selection_must_be_explicit_and_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            platform_path = (
                workspace / "profiles/platforms/cleanroom/provisional.yaml"
            )
            pack_path = (
                workspace / "profiles/packs/supersymmetry/profile.yaml"
            )

            with self.assertRaisesRegex(
                ProjectInspectionError,
                "pack selection must be a non-empty string",
            ):
                inspect_workspace(
                    workspace,
                    platform_profile_path=platform_path,
                    pack_profile_path=pack_path,
                    pack_selection="",
                )
            with self.assertRaisesRegex(
                ProjectInspectionError,
                "pack profile selection does not exist: unknown",
            ):
                inspect_workspace(
                    workspace,
                    platform_profile_path=platform_path,
                    pack_profile_path=pack_path,
                    pack_selection="unknown",
                )

    def test_stale_packwiz_index_is_visible_without_hiding_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            (workspace / "index.toml").write_text(
                "hash-format = \"sha256\"\n",
                encoding="utf-8",
            )

            result = self._inspect(workspace)

            self.assertFalse(
                result["project"]["index"]["matches_declared_hash"]
            )
            self.assertTrue(result["workspace"]["dirty"])

    def test_independent_command_emits_the_same_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = self._workspace(parent, include_profiles=False)
            authority = parent / "authority"
            authority.mkdir()
            platform_path = authority / "platform.yaml"
            pack_path = authority / "pack.yaml"
            platform_path.write_text(PLATFORM_YAML, encoding="utf-8")
            pack_path.write_text(PACK_YAML, encoding="utf-8")
            environment = os.environ.copy()
            source_roots = [str(MODULE_ROOT / "src")]
            if environment.get("PYTHONPATH"):
                source_roots.append(environment["PYTHONPATH"])
            environment["PYTHONPATH"] = os.pathsep.join(source_roots)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workbench_project_intelligence",
                    str(workspace),
                    "--platform-profile",
                    str(platform_path),
                    "--pack-profile",
                    str(pack_path),
                    "--pack-selection",
                    "cleanroom-test",
                    "--compact",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["project"]["name"], "Test Pack")
            self.assertFalse((workspace / "profiles").exists())

    def test_platform_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(Path(temporary))
            pack_path = workspace / "profiles/packs/supersymmetry/profile.yaml"
            pack_path.write_text(
                PACK_YAML.replace(
                    "workbench-platform:cleanroom:test",
                    "workbench-platform:cleanroom:other",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ProjectInspectionError,
                "binds a different platform",
            ):
                self._inspect(workspace)

    def test_non_git_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ProjectInspectionError, "Git"):
                inspect_workspace(
                    Path(temporary),
                    platform_profile_path=Path(temporary) / "platform.yaml",
                    pack_profile_path=Path(temporary) / "pack.yaml",
                    pack_selection="cleanroom-test",
                )


if __name__ == "__main__":
    unittest.main()
