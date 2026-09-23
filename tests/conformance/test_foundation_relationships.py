"""Cross-module conformance for the first Workbench foundation graph."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree

from jsonschema import Draft202012Validator


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SHELL_ROOT = REPOSITORY_ROOT / "modules/workbench-shell"
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence"
)
sys.path.insert(0, str(SHELL_ROOT / "src"))

from workbench_shell.component_graph import load_component_graph  # noqa: E402


class FoundationRelationshipConformanceTest(unittest.TestCase):
    def test_registry_matches_required_relationship_fixture(self) -> None:
        registry_path = SHELL_ROOT / "data/component-registry-v2.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry_schema = json.loads(
            (
                SHELL_ROOT
                / "schemas/component-registry-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(registry_schema).validate(registry)

        graph = load_component_graph(registry_path, REPOSITORY_ROOT)
        expected = json.loads(
            (
                REPOSITORY_ROOT
                / "tests/conformance/client-protocol/relationships-v2.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            graph.dependency_edges(),
            frozenset(tuple(edge) for edge in expected["required_edges"]),
        )
        self.assertEqual(
            graph.shell_component,
            expected["client_entrypoint"],
        )

    def test_client_package_manifests_match_registry(self) -> None:
        graph = load_component_graph(
            SHELL_ROOT / "data/component-registry-v2.json",
            REPOSITORY_ROOT,
        )
        clients = {
            component.id: component
            for component in graph.components
            if component.kind == "client"
        }
        self.assertEqual(
            {"client-vscode", "client-intellij-community"},
            set(clients),
        )
        vscode = json.loads(
            (REPOSITORY_ROOT / clients["client-vscode"].path / "package.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual("workbench-vscode", vscode["name"])
        self.assertEqual("./extension.js", vscode["main"])
        self.assertIn(
            "workbench.workspaceHome.open",
            {row["command"] for row in vscode["contributes"]["commands"]},
        )

        intellij_root = REPOSITORY_ROOT / clients["client-intellij-community"].path
        self.assertTrue((intellij_root / "build.gradle.kts").is_file())
        plugin = ElementTree.parse(
            intellij_root / "src/main/resources/META-INF/plugin.xml"
        ).getroot()
        self.assertEqual("dev.cleanroommc.workbench", plugin.findtext("id"))
        self.assertIn(
            "Workbench.OpenWorkspaceHome",
            {row.get("id") for row in plugin.findall(".//action")},
        )

    def test_foundation_modules_have_executable_source_and_tests(self) -> None:
        for module_root in (SHELL_ROOT, PROJECT_INTELLIGENCE_ROOT):
            self.assertTrue((module_root / "src").is_dir())
            self.assertTrue(any((module_root / "src").rglob("*.py")))
            self.assertTrue((module_root / "tests").is_dir())
            self.assertTrue(any((module_root / "tests").glob("test_*.py")))

    def test_protocol_fixtures_match_the_v2_schema(self) -> None:
        protocol_root = (
            REPOSITORY_ROOT / "tests/conformance/client-protocol"
        )
        schema = json.loads(
            (
                SHELL_ROOT / "schemas/client-protocol-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)
        for name in (
            "initialize-request-v2.json",
            "initialize-response-v2.json",
            "workspace-inspect-request-v2.json",
            "runtime-plan-request-v2.json",
            "runtime-diagnose-request-v2.json",
            "instance-select-request-v2.json",
            "instance-current-request-v2.json",
            "registration-capabilities-request-v2.json",
            "registration-plan-request-v2.json",
            "registration-apply-request-v2.json",
            "shutdown-request-v2.json",
        ):
            with self.subTest(name=name):
                validator.validate(
                    json.loads(
                        (protocol_root / name).read_text(encoding="utf-8")
                    )
                )


if __name__ == "__main__":
    unittest.main()
