"""Core retains the whole reviewed wheelhouse before any package install."""

from __future__ import annotations

from multiprocessing import get_context
import os
from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch

from workbench_core.environment_package_closure import plan_package_closure
from workbench_core.environment_package_import import (
    apply_package_import, plan_package_import, reopen_package_import,
)
from workbench_core.environment_reconstruction import ReconstructionError
from workbench_core.output_routing import _private_directory
from workbench_core.storage.registered import CoreDurableResources

import test_environment_package_closure as closure_fixture


@skipIf(not sys.platform.startswith("linux"), "package import is a Linux/WSL slice")
class EnvironmentPackageImportTests(TestCase):
    def setUp(self) -> None:
        closure_fixture.EnvironmentPackageClosureTests.setUp(self)
        closure_fixture.EnvironmentPackageClosureTests._import(self)
        closure_fixture.EnvironmentPackageClosureTests._assembly(self)
        self.closure = plan_package_closure(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            wheel_resource_id=self.retained["resource"]["resource_id"],
            wheelhouse=self.wheelhouse, environment=self.environment,
        )

    def _plan(self) -> dict:
        return plan_package_import(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace, environment=self.environment,
        )

    def _apply(self, plan: dict) -> dict:
        return apply_package_import(
            self.suite, self.share, self.candidate, self.closure,
            expected_plan_id=plan["plan_id"], workspace=self.workspace,
            environment=self.environment,
        )

    def _reopen(self, result: dict) -> dict:
        return reopen_package_import(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace, result_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_acquire_reopen_reuse_without_original_source_or_install(self) -> None:
        plan = self._plan()
        self.assertEqual("ready", plan["state"])
        self.assertEqual("acquire", plan["action"])
        with patch("workbench_core.module_cli._pip", side_effect=AssertionError("pip must not run")):
            result = self._apply(plan)
        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(self.closure["unresolved_inputs"], result["unresolved_inputs"])
        self.assertIn("optional-module-packages", result["unresolved_inputs"])
        self.assertEqual(result["tree_id"], self._reopen(result)["tree_id"])
        self.wheelhouse.rename(self.root / "former-wheelhouse")
        reuse = self._plan()
        self.assertEqual("reuse", reuse["action"])
        reused = self._apply(reuse)
        self.assertEqual("reused", reused["outcome"])
        self.assertEqual(result["tree_id"], reused["tree_id"])
        self._reopen(reused)

    def test_changed_source_and_stale_plan_refuse_before_copy(self) -> None:
        plan = self._plan()
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_package_import(
                self.suite, self.share, self.candidate, self.closure,
                expected_plan_id="stale", workspace=self.workspace,
                environment=self.environment,
            )
        source = self.wheelhouse / "wheels/helper-1.0-py3-none-any.whl"
        with source.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ReconstructionError):
            self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())

    def test_changed_retained_wheel_and_managed_package_refuse(self) -> None:
        plan = self._plan()
        result = self._apply(plan)
        retained_package = Path(result["tree_path"]) / "wheels/helper-1.0-py3-none-any.whl"
        with retained_package.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)
        with self.assertRaises(ReconstructionError):
            self._plan()

    def test_missing_source_blocks_acquire_and_foreign_target_is_preserved(self) -> None:
        self.wheelhouse.rename(self.root / "former-wheelhouse")
        plan = self._plan()
        self.assertEqual("blocked", plan["state"])
        self.assertIn("unavailable for acquisition", plan["blockers"][0])
        with self.assertRaisesRegex(ReconstructionError, "blocked"):
            self._apply(plan)
        target = Path(plan["target"])
        _private_directory(target.parent)
        target.mkdir(mode=0o700)
        marker = target / "foreign.txt"
        marker.write_text("foreign", encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "outside Core custody"):
            self._plan()
        self.assertEqual("foreign", marker.read_text(encoding="utf-8"))

    def test_redirect_and_public_parent_are_refused(self) -> None:
        plan = self._plan()
        self.wheelhouse.rename(self.root / "real-wheelhouse")
        self.wheelhouse.symlink_to(self.root / "real-wheelhouse", target_is_directory=True)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            self._plan()
        self.wheelhouse.unlink()
        (self.root / "real-wheelhouse").rename(self.wheelhouse)
        target_parent = Path(plan["target"]).parent
        _private_directory(target_parent)
        target_parent.chmod(0o755)
        with self.assertRaisesRegex(ReconstructionError, "owner-private"):
            self._plan()
        self.assertFalse(Path(plan["target"]).exists())

    def test_source_parent_redirect_after_review_is_refused(self) -> None:
        plan = self._plan()
        from workbench_core import environment_package_import as importer
        original = importer._copy_assembly

        def redirect(stage, source, reviewed):
            parent = source / "wheels"
            relocated = source / "wheels-real"
            parent.rename(relocated)
            parent.symlink_to(relocated, target_is_directory=True)
            original(stage, source, reviewed)

        with patch.object(importer, "_copy_assembly", redirect):
            with self.assertRaises(ReconstructionError):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())

    def test_extra_stage_member_and_exact_tree_bound_refuse(self) -> None:
        plan = self._plan()
        from workbench_core import environment_package_import as importer
        original = importer._copy_assembly

        def extra(stage, source, reviewed):
            original(stage, source, reviewed)
            (stage / "extra-empty").mkdir(mode=0o700)

        with patch.object(importer, "_copy_assembly", extra):
            with self.assertRaisesRegex(ReconstructionError, "extra or missing members"):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())
        with patch.object(importer, "MAX_TOTAL_BYTES", 1024):
            blocked = self._plan()
        self.assertEqual("blocked", blocked["state"])
        self.assertIn("exact-tree byte bound", blocked["blockers"][0])

    def test_failed_stage_is_classified_and_can_retry(self) -> None:
        plan = self._plan()
        from workbench_core import environment_package_import as importer
        original = importer._copy_assembly

        def interrupted(stage, source, reviewed):
            original(stage, source, reviewed)
            raise RuntimeError("interrupted before tree intent")

        with patch.object(importer, "_copy_assembly", interrupted):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan)
        retry = self._plan()
        self.assertEqual("acquire", retry["action"])
        self.assertEqual(1, len(retry["prior_failed_trees"]))
        result = self._apply(retry)
        self.assertEqual("acquired", result["outcome"])
        self._reopen(result)

    def test_result_failure_after_tree_commit_reuses_without_source(self) -> None:
        plan = self._plan()
        original = CoreDurableResources.publish_bytes

        def fail_result(service, role, name, data, **kwargs):
            if name == "environment-package-import.json":
                raise RuntimeError("interrupted after tree commit")
            return original(service, role, name, data, **kwargs)

        with patch.object(CoreDurableResources, "publish_bytes", fail_result):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan)
        self.wheelhouse.rename(self.root / "former-wheelhouse")
        retry = self._plan()
        self.assertEqual("reuse", retry["action"])
        result = self._apply(retry)
        self.assertEqual("reused", result["outcome"])
        self._reopen(result)

    def test_hard_exit_before_rename_reconciles_exact_stage(self) -> None:
        plan = self._plan()

        def interrupted() -> None:
            from workbench_core import managed_trees
            with patch.object(managed_trees, "_rename_no_replace",
                              side_effect=lambda *args, **kwargs: os._exit(73)):
                self._apply(plan)

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(73, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        result = self._apply(restarted)
        self.assertEqual("reconciled", result["outcome"])
        self._reopen(result)

    def test_changed_interrupted_stage_refuses_reconcile(self) -> None:
        plan = self._plan()

        def interrupted() -> None:
            from workbench_core import managed_trees
            with patch.object(managed_trees, "_rename_no_replace",
                              side_effect=lambda *args, **kwargs: os._exit(73)):
                self._apply(plan)

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(73, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        from workbench_core.environment_package_import import _store
        from workbench_core.environment_resolution import resolve_environment
        local = resolve_environment(self.suite, workspace=self.workspace, environment=self.environment)
        host, _, _ = _store(local)
        row = next(row for row in host.catalog.trees.inventory(workspace=self.workspace)
                   if row["tree_id"] == restarted["tree_id"])
        staged = Path(row["staging"]) / "wheels/helper-1.0-py3-none-any.whl"
        with staged.open("ab") as output:
            output.write(b"changed")
        with self.assertRaisesRegex(ReconstructionError, "cannot be reopened"):
            self._apply(restarted)
        self.assertFalse(Path(plan["target"]).exists())

    def test_hard_exit_after_rename_reconciles_commit(self) -> None:
        plan = self._plan()

        def interrupted() -> None:
            from workbench_core.storage.tree_catalog import TreeCatalog
            original = TreeCatalog._write

            def exit_commit(catalog, section, *args, **kwargs):
                if section == "commits":
                    os._exit(74)
                return original(catalog, section, *args, **kwargs)

            with patch.object(TreeCatalog, "_write", exit_commit):
                self._apply(plan)

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(74, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        self.assertTrue(Path(plan["target"]).is_dir())
        result = self._apply(restarted)
        self.assertEqual("reconciled", result["outcome"])
        self._reopen(result)
