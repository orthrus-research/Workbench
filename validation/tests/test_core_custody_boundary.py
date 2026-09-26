"""Keep migrated owner code behind the public Core custody ports."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (ROOT / "modules", ROOT / "profiles")
PHYSICAL_BACKENDS = {
    "workbench_core.durable_records",
    "workbench_core.host_filesystem",
    "workbench_core.managed_trees",
    "workbench_core.verified_artifact_host",
    "workbench_core.working_allocations",
}
PHYSICAL_BACKEND_NAMES = {name.removeprefix("workbench_core.") for name in PHYSICAL_BACKENDS}


class CoreCustodyBoundaryTests(unittest.TestCase):
    def test_domain_source_does_not_import_core_physical_backends(self) -> None:
        violations: list[str] = []
        for root in SOURCE_ROOTS:
            for path in root.glob("**/src/**/*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        imported = {alias.name for alias in node.names}
                        forbidden = (
                            node.module in PHYSICAL_BACKENDS
                            or node.module == "workbench_core"
                            and bool(imported & PHYSICAL_BACKEND_NAMES)
                            or node.module == "workbench_core.artifact_store"
                            and "fetch_verified_artifact" in imported
                        )
                    elif isinstance(node, ast.Import):
                        forbidden = any(
                            alias.name in PHYSICAL_BACKENDS
                            or alias.name == "workbench_core.artifact_store"
                            for alias in node.names
                        )
                    else:
                        continue
                    if forbidden:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
