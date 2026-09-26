"""The direct CLI custody scope offers the same managed-tree port as dispatch."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.managed_trees import ManagedTreeError, managed_trees
from workbench_core.host_services import direct_module_custody_scope


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


if __name__ == "__main__":
    unittest.main()
