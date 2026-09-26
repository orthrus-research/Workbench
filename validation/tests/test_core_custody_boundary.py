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
DIRECT_CORE_COMPATIBILITY = {
    # Legacy direct entry and reader composition. These exact imports remain
    # visible until their selected-context adapters move behind the API.
    "modules/atlas/src/workbench_atlas/layout.py": {
        ("workbench_core.environment_resolution", "resolve_environment"),
    },
    "modules/atlas/src/workbench_atlas_recipe_health/cli.py": {
        ("workbench_core.host_services", "direct_atlas_derived_index_scope"),
    },
    "modules/atlas/src/workbench_atlas_observations/cli.py": {
        ("workbench_core.host_services", "direct_atlas_derived_index_scope"),
    },
    "modules/blueprints/src/workbench_blueprints/cli.py": {
        # The supported direct CLI composes Core after validating its target.
        ("workbench_core.host_services", "direct_module_custody_scope"),
    },
    "modules/runtime-explorer/src/workbench_runtime_explorer/graph_query.py": {
        ("workbench_core.service.runtime", "ServiceRuntimeV3"),
    },
    "modules/pack-program-studio/src/workbench_pack_program_studio/cli.py": {
        ("workbench_core.host_services", "install_local_host_services"),
        ("workbench_core.host_services", "resolve_local_working_allocations"),
    },
    "modules/relay/src/workbench_relay/cli.py": {
        ("workbench_core.host_services", "install_local_host_services"),
    },
}


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

    def test_domain_core_imports_are_explicit_compatibility_entries(self) -> None:
        """New domain work uses the Core API; known direct hosts stay reviewable."""

        violations: list[str] = []
        for root in SOURCE_ROOTS:
            for path in root.glob("**/src/**/*.py"):
                relative = path.relative_to(ROOT).as_posix()
                if relative.startswith("modules/workbench-shell/"):
                    # Shell is the compatibility composition module. Its older
                    # direct Core imports are reconciled by migration family.
                    continue
                allowed = DIRECT_CORE_COMPATIBILITY.get(relative, set())
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        if module == "workbench_core" or module.startswith("workbench_core."):
                            for alias in node.names:
                                if (module, alias.name) not in allowed:
                                    violations.append(f"{relative}:{node.lineno}:{module}.{alias.name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == "workbench_core" or alias.name.startswith("workbench_core."):
                                violations.append(f"{relative}:{node.lineno}:{alias.name}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
