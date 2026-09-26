"""The direct CLI custody scope offers the same managed-tree port as dispatch."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.managed_trees import ManagedTreeError, managed_trees, managed_trees_scope
from workbench_core.host_services import direct_module_custody_scope, suite_managed_tree_scope
from workbench_core.managed_trees import CoreManagedTrees


class DirectModuleManagedTreesTests(unittest.TestCase):
    def test_scope_binds_workspace_and_owner_and_releases_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            home = root / "config"
            with patch("workbench_core.host_services.install_local_host_services"):
                with direct_module_custody_scope(
                    workspace=workspace, owner_id="workbench-shell",
                    environment={"WORKBENCH_CONFIG_HOME": str(home)},
                ):
                    provider = managed_trees()
                    self.assertEqual(provider.workspace, workspace)
                    self.assertEqual(provider.owner_id, "workbench-shell")
                    self.assertEqual(provider.catalog.configuration_home, home)
                    self.assertEqual(provider.locations, {"artifacts": workspace})
                with self.assertRaises(ManagedTreeError) as unbound:
                    managed_trees()
                self.assertEqual(unbound.exception.code, "tree.host")

    def test_suite_scope_uses_suite_workspace_and_dispatch_catalog_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = root / "suite"
            target = root / "target"
            home = root / "config"
            suite.mkdir()
            target.mkdir()
            outer = CoreManagedTrees(
                workspace=target, configuration_home=home,
                locations={"artifacts": target}, owner_id="workbench-shell",
            )
            with managed_trees_scope(outer):
                with suite_managed_tree_scope(workspace=suite, configuration_home=home):
                    provider = managed_trees()
                    self.assertEqual(provider.workspace, suite)
                    self.assertEqual(provider.catalog.configuration_home, home)
                    self.assertEqual(provider.locations, {"artifacts": suite})
                self.assertIs(managed_trees(), outer)


if __name__ == "__main__":
    unittest.main()
