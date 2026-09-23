"""Focused tests for Workbench component dependency enforcement."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.component_graph import (  # noqa: E402
    Component,
    ComponentGraph,
    ComponentGraphError,
    load_component_graph,
)


REGISTRY = MODULE_ROOT / "data/component-registry-v2.json"


class ComponentGraphTest(unittest.TestCase):
    def test_repository_graph_and_clients_are_valid(self) -> None:
        graph = load_component_graph(REGISTRY, REPOSITORY_ROOT)

        self.assertEqual(graph.shell_component, "workbench-shell")
        self.assertLess(
            graph.dependency_order().index("project-intelligence"),
            graph.dependency_order().index("workbench-shell"),
        )
        self.assertEqual(
            {
                (component.id, component.path, component.state)
                for component in graph.components
                if component.kind == "client"
            },
            {
                ("client-vscode", "clients/vscode", "review"),
                (
                    "client-intellij-community",
                    "clients/intellij-community",
                    "review",
                ),
            },
        )

    def test_cycle_is_rejected(self) -> None:
        graph = ComponentGraph(
            registry_id="test",
            lifecycle_states=("active",),
            shell_component="a",
            project_context_component="b",
            authority_components=(),
            profile_roots=(),
            components=(
                Component("a", "foundation", "modules/a", "active", ("b",)),
                Component("b", "foundation", "modules/b", "active", ("a",)),
            ),
        )

        with self.assertRaisesRegex(ComponentGraphError, "cycle"):
            graph.dependency_order()

    def test_missing_component_path_fails_closed(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        payload["components"][0]["path"] = "modules/does-not-exist"
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "registry.json"
            candidate.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ComponentGraphError,
                "path does not exist",
            ):
                load_component_graph(candidate, REPOSITORY_ROOT)


if __name__ == "__main__":
    unittest.main()
