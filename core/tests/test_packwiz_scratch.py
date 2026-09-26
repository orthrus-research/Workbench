"""Packwiz source scratch uses the selected Core catalog and stays retained."""

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module
from workbench_api.temporary_leases import (
    TemporaryScratchError, packwiz_source_scratch,
)
from workbench_core.host_services import direct_packwiz_scratch_scope
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.storage.registered import ResourceCatalog


PLAN = "a" * 64


class PackwizScratchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.state = self.root / "state"
        self.config = self.root / "selected-config"

    def test_direct_scope_retains_exact_source_and_selected_catalog(self) -> None:
        with self.assertRaises(TemporaryScratchError):
            packwiz_source_scratch(
                workspace=self.workspace, state_root=self.state, plan_digest=PLAN,
            )
        with direct_packwiz_scratch_scope(configuration_home=self.config):
            with packwiz_source_scratch(
                workspace=self.workspace, state_root=self.state, plan_digest=PLAN,
            ) as source:
                (source / "source.txt").write_bytes(b"copied")
        self.assertEqual(b"copied", (source / "source.txt").read_bytes())
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["temporary_leases"]
        self.assertEqual([(str(source), "retained-unproven")], [
            (row["path"], row["status"]) for row in rows
        ])

    def test_failed_owner_retains_scratch_for_review(self) -> None:
        with direct_packwiz_scratch_scope(configuration_home=self.config):
            with self.assertRaisesRegex(RuntimeError, "domain failure"):
                with packwiz_source_scratch(
                    workspace=self.workspace, state_root=self.state,
                    plan_digest=PLAN,
                ) as source:
                    (source / "source.txt").write_bytes(b"partial")
                    raise RuntimeError("domain failure")
        retained = json.loads((source / ".workbench-temporary-retained.json").read_bytes())
        self.assertEqual("failed", retained["outcome"])
        self.assertEqual("process-absence-unproven", retained["reason"])
        self.assertEqual(b"partial", (source / "source.txt").read_bytes())

    def test_wsl_like_private_mode_failure_refuses_before_allocation(self) -> None:
        with direct_packwiz_scratch_scope(configuration_home=self.config):
            with patch("workbench_core.temporary_leases.private_path", return_value=False):
                with self.assertRaisesRegex(TemporaryScratchError, "on WSL use a Linux filesystem"):
                    with packwiz_source_scratch(
                        workspace=self.workspace, state_root=self.state,
                        plan_digest=PLAN,
                    ):
                        self.fail("unsafe mount must not become scratch")
        self.assertEqual([], list((self.state / "staging/packwiz-v2").iterdir()))

    def test_installed_shell_dispatch_binds_selected_configuration_home(self) -> None:
        module = Module("workbench-shell", "0.1.0", (
            Capability("shell.scratch", ("scratch",), "scratch_plugin:run", "scratch"),
        ))
        installed = (InstalledModule(
            "workbench-shell", "workbench-shell", "0.1.0", "available", module=module,
        ),)
        context = ExecutionContext(
            self.workspace, self.state, configuration_home=self.config,
        )
        paths = []

        def run(_arguments, *, context):
            with packwiz_source_scratch(
                workspace=context.workspace, state_root=context.state_root,
                plan_digest=PLAN,
            ) as source:
                (source / "source.txt").write_bytes(b"dispatched")
                paths.append(source)
            return 0

        with patch("workbench_core.modules.import_module", return_value=SimpleNamespace(run=run)):
            self.assertEqual(0, dispatch(["scratch"], context, installed))
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["temporary_leases"]
        self.assertEqual([(str(paths[0]), "retained-unproven")], [
            (row["path"], row["status"]) for row in rows
        ])


if __name__ == "__main__":
    unittest.main()
