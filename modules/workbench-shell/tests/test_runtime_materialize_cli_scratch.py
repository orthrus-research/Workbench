"""Direct runtime materialization binds Core scratch to its selected home."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/project-intelligence/src"))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_api.temporary_leases import packwiz_source_scratch  # noqa: E402
from workbench_api.managed_trees import managed_trees, managed_trees_scope  # noqa: E402
from workbench_core.configuration import load_workbench_configuration  # noqa: E402
from workbench_core.managed_trees import CoreManagedTrees  # noqa: E402
from workbench_core.storage.registered import ResourceCatalog  # noqa: E402
from workbench_shell.cli import main as cli_main  # noqa: E402


class DirectRuntimeScratchTests(unittest.TestCase):
    def test_direct_materialize_uses_selected_configuration_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "pack"
            workspace.mkdir()
            packwiz = root / "packwiz"
            packwiz.write_bytes(b"tool")
            selected = root / "selected-config"
            result = {"format": "test-runtime-materialization", "outcome": "installed"}
            source_paths = []
            tree_hosts = []

            def materialize(_suite, selected_workspace, **_kwargs):
                tree_hosts.append(managed_trees())
                with packwiz_source_scratch(
                    workspace=selected_workspace,
                    state_root=root / "runtime-state",
                    plan_digest="a" * 64,
                ) as source:
                    (source / "source.txt").write_bytes(b"copy")
                    source_paths.append(source)
                return result

            output = StringIO()
            configuration = load_workbench_configuration(REPOSITORY_ROOT)
            with (
                patch("workbench_shell.cli.load_workbench_configuration", return_value=configuration),
                patch("workbench_shell.cli._require_manual_artifact_preflight"),
                patch("workbench_shell.cli.materialize_project_runtime", side_effect=materialize),
                redirect_stdout(output),
            ):
                status = cli_main([
                    "runtime-materialize", str(workspace),
                    "--suite-root", str(REPOSITORY_ROOT),
                    "--packwiz", str(packwiz), "--json",
                ], runtime_configuration_home=selected)
            self.assertEqual(0, status)
            self.assertEqual(result, json.loads(output.getvalue()))
            self.assertEqual(selected, tree_hosts[0].catalog.configuration_home)
            self.assertEqual(workspace, tree_hosts[0].workspace)
            rows = ResourceCatalog(selected).inventory(workspace=workspace)["temporary_leases"]
            self.assertEqual([(str(source_paths[0]), "retained-unproven")], [
                (row["path"], row["status"]) for row in rows
            ])

    def test_installed_dispatch_tree_binding_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "pack"
            workspace.mkdir()
            packwiz = root / "packwiz"
            packwiz.write_bytes(b"tool")
            selected = root / "dispatch-config"
            bound = CoreManagedTrees(
                workspace=workspace, configuration_home=selected,
                locations={"artifacts": workspace}, owner_id="workbench-shell",
            )
            configuration = load_workbench_configuration(REPOSITORY_ROOT)
            with (
                managed_trees_scope(bound),
                patch("workbench_shell.cli.load_workbench_configuration", return_value=configuration),
                patch("workbench_shell.cli._require_manual_artifact_preflight"),
                patch("workbench_shell.cli.materialize_project_runtime", side_effect=lambda *_args, **_kwargs: {
                    "bound": managed_trees() is bound,
                }),
                redirect_stdout(StringIO()) as output,
            ):
                status = cli_main([
                    "runtime-materialize", str(workspace),
                    "--suite-root", str(REPOSITORY_ROOT),
                    "--packwiz", str(packwiz), "--json",
                ], runtime_configuration_home=root / "different-direct-config")
            self.assertEqual(0, status)
            self.assertEqual({"bound": True}, json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
