"""Focused composition test for the read-only Workbench foundation."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from supersymmetry_project_fixture import (  # noqa: E402
    create_supersymmetry_project,
)
from workbench_shell import inspect_project  # noqa: E402
from workbench_core.configuration import (  # noqa: E402
    load_workbench_configuration,
)


CANARY_CONFIG = """\
schema = "workbench/config/v1"

[selection]
pack_document = "profiles/packs/canary/profile.yaml"
pack_variant = "renamed-current"
platform_document = "profiles/platforms/canary/current.yaml"

[bindings]
"""

CANARY_PACK_PROFILE = """\
schema_version: 1
profile_family_id: workbench-pack:canary
display_name: Canary Pack
status: test
workspace:
  kind: packwiz-modpack
  expected_name: Canary Pack
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
  renamed-current:
    platform_profile_id: workbench-platform:canary:current
    maturity: experimental
    permitted_operations:
      - observe
"""

CANARY_PLATFORM_PROFILE = """\
schema_version: 1
profile_id: workbench-platform:canary:current
kind: canary
status: test
minecraft_version: 1.12.2
cleanroom_version: 9.9.9-canary
"""


def _create_canary_suite(parent: Path) -> Path:
    suite = parent / "suite"
    registry = suite / "modules/workbench-shell/data/component-registry-v2.json"
    registry.parent.mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT
        / "modules/workbench-shell/data/component-registry-v2.json",
        registry,
    )
    for component in (
        "modules/atlas",
        "modules/blueprints",
        "modules/manuals",
        "modules/project-intelligence",
        "modules/workbench-shell",
    ):
        (suite / component).mkdir(parents=True, exist_ok=True)
    for client in ("vscode", "intellij-community"):
        (suite / f"clients/{client}").mkdir(parents=True)

    pack_profile = suite / "profiles/packs/canary/profile.yaml"
    platform_profile = suite / "profiles/platforms/canary/current.yaml"
    pack_profile.parent.mkdir(parents=True)
    platform_profile.parent.mkdir(parents=True)
    pack_profile.write_text(CANARY_PACK_PROFILE, encoding="utf-8")
    platform_profile.write_text(CANARY_PLATFORM_PROFILE, encoding="utf-8")
    (suite / "canary.toml").write_text(CANARY_CONFIG, encoding="utf-8")
    return suite


def _rename_fixture_project(project: Path) -> None:
    pack_toml = project / "pack.toml"
    pack_toml.write_text(
        pack_toml.read_text(encoding="utf-8").replace(
            'name = "Supersymmetry"',
            'name = "Canary Pack"',
        ),
        encoding="utf-8",
    )


class BootstrapTest(unittest.TestCase):
    def test_repository_context_is_composed_through_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_supersymmetry_project(Path(temporary))
            result = inspect_project(REPOSITORY_ROOT, project)

            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(
                result["workspace_context"]["project"]["name"],
                "Supersymmetry",
            )
            self.assertEqual(
                result["workspace_context"]["platform"]["profile_id"],
                "workbench-platform:cleanroom:provisional",
            )
            self.assertEqual(
                result["workspace_context"]["pack"]["platform_profile_id"],
                result["workspace_context"]["platform"]["profile_id"],
            )
            self.assertEqual(
                {client["component_id"] for client in result["clients"]},
                {"client-vscode", "client-intellij-community"},
            )
            self.assertFalse((project / "profiles").exists())

    def test_explicit_configuration_selects_renamed_canary_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = _create_canary_suite(root)
            project = create_supersymmetry_project(root)
            _rename_fixture_project(project)

            result = inspect_project(
                suite,
                project,
                config_path="canary.toml",
            )
            preloaded = load_workbench_configuration(suite, "canary.toml")
            preloaded_result = inspect_project(
                suite,
                project,
                configuration=preloaded,
            )

            context = result["workspace_context"]
            self.assertEqual(context, preloaded_result["workspace_context"])
            self.assertEqual(context["project"]["name"], "Canary Pack")
            self.assertEqual(
                context["pack"]["profile_family_id"],
                "workbench-pack:canary",
            )
            self.assertEqual(
                context["pack"]["selected_profile"],
                "renamed-current",
            )
            self.assertEqual(
                context["platform"]["profile_id"],
                "workbench-platform:canary:current",
            )
            self.assertEqual(
                context["platform"]["cleanroom_version"],
                "9.9.9-canary",
            )

    def test_preloaded_configuration_uses_snapshotted_profile_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = _create_canary_suite(root)
            project = create_supersymmetry_project(root)
            _rename_fixture_project(project)
            configuration = load_workbench_configuration(
                suite,
                "canary.toml",
            )
            profile_path = configuration.platform_document.source.path
            profile_path.write_text(
                profile_path.read_text(encoding="utf-8") + "# drift\n",
                encoding="utf-8",
            )

            result = inspect_project(
                suite,
                project,
                configuration=configuration,
            )

            self.assertEqual(
                result["workspace_context"]["platform"]["cleanroom_version"],
                "9.9.9-canary",
            )
            self.assertEqual(
                result["workspace_context"]["platform"]["document_sha256"],
                configuration.platform_document.source.sha256,
            )


if __name__ == "__main__":
    unittest.main()
