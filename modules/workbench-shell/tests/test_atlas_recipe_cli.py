"""Shell's optional owner-plan composition uses the independent Atlas CLI."""

from __future__ import annotations

from io import StringIO
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
sys.path.insert(0, str(ROOT / "modules/atlas/tests"))

from workbench_api import Capability, ExecutionContext, Module
from workbench_shell import atlas_recipe_cli
from workbench_shell import atlas_recipe_plan_assessment
from workbench_shell import commands
from workbench_atlas_recipe_health import cli as atlas_cli


class AtlasRecipePlanCliTests(unittest.TestCase):
    def _discover_composition(self, *, legacy_shell=False, disabled=()):
        from workbench_core.modules import discover
        import workbench_registration_atlas
        import workbench_registration_workbench_shell

        descriptors = []
        for registration in (workbench_registration_atlas, workbench_registration_workbench_shell):
            with mock.patch.object(registration, "version", return_value="0.1.0"):
                descriptors.append(registration.module())
        if legacy_shell:
            descriptors[1] = replace(descriptors[1], capabilities=(Capability(
                "workbench-shell.atlas-recipes", ("atlas", "recipes"),
                "workbench_shell.commands:atlas_recipes", "Run atlas recipes",
            ),))
        owners = {module.id for module in descriptors}
        dependencies = {required for module in descriptors for required in module.requires} - owners
        descriptors.extend(Module(owner, "0.1.0") for owner in sorted(dependencies))
        entries = [SimpleNamespace(
            name=module.id,
            value=f"registration_{module.id}:module",
            load=lambda module=module: lambda: module,
            dist=SimpleNamespace(metadata={"Name": "workbench-" + module.id}, version="0.1.0", requires=()),
        ) for module in descriptors]
        return discover(entries=entries, disabled=disabled)

    def _main(self, *arguments: str) -> tuple[int, str, str]:
        output = StringIO()
        error = StringIO()
        status = atlas_recipe_cli.main(arguments, output=output, error=error)
        return status, output.getvalue(), error.getvalue()

    def test_assess_plan_delegates_only_to_owner_validated_adapter(self) -> None:
        from atlas_recipe_assessment_fixture import assessment_fixture
        record = assessment_fixture()
        with mock.patch.object(
            atlas_recipe_plan_assessment,
            "assess_recipe_change_plan",
            return_value=record,
        ) as assessed:
            status, output, error = self._main(
                "assess-plan",
                "/graph",
                "workbench-plan:sha256:" + "1" * 64,
                "--state-root",
                "/state",
                "--max-depth",
                "3",
                "--max-nodes",
                "100",
                "--json",
            )
        self.assertEqual(status, 0, error)
        self.assertEqual(record, json.loads(output))
        assessed.assert_called_once_with(
            ROOT,
            Path("/graph"),
            Path("/state"),
            "workbench-plan:sha256:" + "1" * 64,
            max_depth=3,
            max_nodes=100,
        )

    def test_registered_plan_route_restores_action_and_passes_context(self) -> None:
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        with mock.patch.object(atlas_recipe_cli, "main", return_value=0) as invoked:
            self.assertEqual(0, commands.atlas_recipe_plan(["/graph", "plan"], context=context))
        invoked.assert_called_once_with(
            ["assess-plan", "/graph", "plan"], suite_root=ROOT, context=context
        )

    def test_legacy_python_entry_point_delegates_independent_actions(self) -> None:
        record = {"context_type": "categorical-graph-v2", "root": "/graph"}
        with mock.patch.object(
            atlas_cli, "discover_recipe_health_operational_context", return_value=record
        ):
            status, output, error = self._main("context", "/graph", "--json")
        self.assertEqual(0, status, error)
        self.assertEqual(record, json.loads(output))

    def test_full_install_routes_only_plan_composition_to_shell(self) -> None:
        from workbench_core.modules import dispatch

        installed = self._discover_composition()
        self.assertTrue(all(row.state == "available" for row in installed))
        routes = [capability.command for row in installed for capability in row.module.capabilities]
        self.assertEqual(len(routes), len(set(routes)))
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        with mock.patch.object(atlas_cli, "main", return_value=0) as atlas_main, mock.patch.object(
            atlas_recipe_cli, "main", return_value=0
        ) as composed, mock.patch(
            "workbench_api.profiles.profiles", return_value=[SimpleNamespace(id="supersymmetry")]
        ):
            self.assertEqual(0, dispatch(["atlas", "recipes", "context", "/graph"], context, installed))
            atlas_main.assert_called_once_with(["context", "/graph"], context=context)
            composed.assert_not_called()
            self.assertEqual(0, dispatch(["atlas", "recipes", "assess-plan", "/graph", "plan"], context, installed))
            composed.assert_called_once_with(
                ["assess-plan", "/graph", "plan"], suite_root=ROOT, context=context
            )

    def test_atlas_upgrade_coexists_with_enabled_legacy_shell_recipe_route(self) -> None:
        from workbench_core.modules import dispatch

        installed = self._discover_composition(legacy_shell=True)
        self.assertTrue(all(row.state == "available" for row in installed))
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        with mock.patch.object(atlas_recipe_cli, "main", return_value=0) as composed, mock.patch.object(
            atlas_cli, "main", return_value=0
        ) as atlas_main:
            self.assertEqual(0, dispatch(["atlas", "recipes", "context", "/graph"], context, installed))
            composed.assert_called_once_with(["context", "/graph"], suite_root=ROOT, context=context)
            atlas_main.assert_not_called()

    def test_disabled_legacy_or_current_shell_leaves_atlas_recipe_route_available(self) -> None:
        from workbench_core.modules import dispatch

        context = ExecutionContext(ROOT, ROOT / ".workbench")
        for legacy_shell in (True, False):
            with self.subTest(legacy_shell=legacy_shell):
                installed = self._discover_composition(legacy_shell=legacy_shell, disabled=("workbench-shell",))
                statuses = {row.id: row.state for row in installed}
                self.assertEqual("available", statuses["atlas"])
                self.assertEqual("disabled", statuses["workbench-shell"])
                with mock.patch.object(atlas_cli, "main", return_value=0) as atlas_main, mock.patch.object(
                    atlas_recipe_cli, "main", return_value=0
                ) as composed:
                    self.assertEqual(0, dispatch(["atlas", "recipes", "context", "/graph"], context, installed))
                    atlas_main.assert_called_once_with(["context", "/graph"], context=context)
                    composed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
