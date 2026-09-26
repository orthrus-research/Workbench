"""Exact optional wheels can be retained without claiming package installation."""

from __future__ import annotations

from copy import deepcopy
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch
import os
import sys
from zipfile import ZipFile

from workbench_core.environment_input_candidates import validate_input_candidate
from workbench_core.environment_reconstruction import (
    ReconstructionError, _seal, build_share,
)
from workbench_core.environment_wheel_import import (
    apply_wheel_import, plan_wheel_import, reopen_wheel_import,
)
from workbench_core.storage.registered import CoreDurableResources
from workbench_core.user_preferences import register_workspace
from workbench_core.output_routing import _private_directory

from test_environment_input_candidates import _wheel
from test_environment_reconstruction import SOURCE_SUITE, _environment, _suite


@skipIf(not sys.platform.startswith("linux"), "wheel import is a Linux/WSL slice")
class EnvironmentWheelImportTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.suite = _suite(self.root / "suite")
        profile = self.suite / "profiles/packs/supersymmetry/profile.yaml"
        original = profile.read_text(encoding="utf-8")
        profile.write_text(original.replace(
            "    maturity: experimental\n",
            "    source_lock: source-locks/legacy-forge/source-lock.json\n"
            "    maturity: experimental\n", 1,
        ), encoding="utf-8")
        source = SOURCE_SUITE / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
        destination = self.suite / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(source.read_bytes())
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.environment = _environment(self.root / "user")
        register_workspace("pack", str(self.workspace), environment=self.environment)
        self.share = build_share(
            self.suite, "pack", environment=self.environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        self.wheel = _wheel(self.root / "workbench_demo-0.1.0-py3-none-any.whl")
        from workbench_core.environment_input_candidates import _wheel_candidate
        platform = self.share["lock"]["platform_profile"]
        fixture = {
            "selected_platform_profile_id": platform["profile_id"],
            "owner_profile_id": "cleanroom",
            "owner_code": {
                "profile_id": "cleanroom", "group": "workbench.workspace_home_fixtures",
                "module": "fixture_owner", "distribution": "workbench-profile-cleanroom",
                "version": "0.1.1", "api_version": 1,
                "sha256": "a" * 64, "size": 100,
                "package_source_sha256": "b" * 64,
            },
            "declaration_id": "fixture.demo", "tree_digest": "sha256:" + "c" * 64,
            "sources": [
                {"kind": kind, "relative_path": f"profiles/platforms/cleanroom/{name}",
                 "sha256": "sha256:" + digest * 64, "size": 1}
                for kind, name, digest in (
                    ("fixture-owner-lock", "fixture-lock.json", "d"),
                    ("fixture-owner-schema", "fixture-schema.json", "e"),
                    ("profile-preflight-tool", "fixture-tool.py", "f"),
                )
            ],
        }
        fixture["sources"].sort(key=lambda row: row["relative_path"].encode("utf-8"))
        self.candidate = _seal({
            "format": "workbench-environment-input-candidate-v1", "schema_version": 1,
            "share_id": self.share["share_id"],
            "host_variant": self.share["lock"]["host_variant"],
            "coverage": "reviewed-input-candidates-only",
            "packages": [_wheel_candidate(self.wheel)], "profile_fixture": fixture,
        }, "workbench-environment-input-candidate", "candidate_id")
        validate_input_candidate(self.share, self.candidate)

    def _plan(self, wheels=None):
        return plan_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            wheels=wheels, environment=self.environment,
        )

    def _apply(self, plan, wheels=None):
        return apply_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            wheels=wheels, expected_plan_id=plan["plan_id"], environment=self.environment,
        )

    def test_acquire_reopen_and_reuse_without_installing(self) -> None:
        plan = self._plan((self.wheel,))
        self.assertEqual("acquire", plan["action"])
        with patch("workbench_core.module_cli._pip", side_effect=AssertionError("pip must not run")):
            result = self._apply(plan, (self.wheel,))
        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        reopened = reopen_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            result_resource_id=result["resource"]["resource_id"], environment=self.environment,
        )
        self.assertEqual(result["tree_id"], reopened["tree_id"])
        self.wheel.unlink()
        reuse = self._plan()
        self.assertEqual("reuse", reuse["action"])
        reused = self._apply(reuse)
        self.assertEqual("reused", reused["outcome"])
        self.assertEqual(result["tree_id"], reused["tree_id"])

    def test_changed_and_redirected_source_refuse_review(self) -> None:
        plan = self._plan((self.wheel,))
        with ZipFile(self.wheel, "a") as archive:
            archive.writestr("demo/changed.py", b"changed\n")
        with self.assertRaisesRegex(ReconstructionError, "candidate"):
            self._apply(plan, (self.wheel,))
        with self.assertRaisesRegex(ReconstructionError, "candidate"):
            self._plan((self.wheel,))

    @skipIf(os.name == "nt", "symlink fixture requires a separate Windows host")
    def test_symlink_source_is_refused(self) -> None:
        alias = self.root / "workbench_demo-0.1.0-py3-none-any.whl"
        other = self.root / "other" / alias.name
        other.parent.mkdir()
        alias.rename(other)
        alias.symlink_to(other)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            self._plan((alias,))

    def test_existing_target_is_preserved(self) -> None:
        plan = self._plan((self.wheel,))
        target = Path(plan["target"])
        _private_directory(target.parent)
        target.mkdir(mode=0o700)
        marker = target / "foreign.txt"
        marker.write_text("foreign", encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "outside completed Core custody"):
            self._plan((self.wheel,))
        with self.assertRaises(ReconstructionError):
            self._apply(plan, (self.wheel,))
        self.assertEqual("foreign", marker.read_text(encoding="utf-8"))

    def test_changed_managed_wheel_refuses_reopen_and_reuse(self) -> None:
        result = self._apply(self._plan((self.wheel,)), (self.wheel,))
        retained = Path(result["tree_path"]) / self.wheel.name
        with retained.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ReconstructionError):
            reopen_wheel_import(
                self.suite, self.share, self.candidate, workspace=self.workspace,
                result_resource_id=result["resource"]["resource_id"],
                environment=self.environment,
            )
        with self.assertRaises(ReconstructionError):
            self._plan()

    def test_unsupported_tree_host_is_blocked_before_copy(self) -> None:
        with patch("workbench_core.environment_wheel_import._tree_host_supported",
                   return_value=False):
            plan = self._plan((self.wheel,))
            self.assertEqual("blocked", plan["state"])
            self.assertIn("exact Linux managed-tree publication is unavailable on this host",
                          plan["blockers"])
            with self.assertRaisesRegex(ReconstructionError, "blocked"):
                self._apply(plan, (self.wheel,))
        self.assertFalse(Path(plan["target"]).exists())

    @skipIf(os.name == "nt", "POSIX mode fixture requires a Linux host")
    def test_public_managed_input_parent_is_refused(self) -> None:
        plan = self._plan((self.wheel,))
        target_parent = Path(plan["target"]).parent
        _private_directory(target_parent)
        target_parent.chmod(0o755)
        with self.assertRaisesRegex(ReconstructionError, "owner-private"):
            self._plan((self.wheel,))
        with self.assertRaises(ReconstructionError):
            self._apply(plan, (self.wheel,))
        self.assertFalse(Path(plan["target"]).exists())

    def test_result_failure_after_tree_commit_can_reopen_and_reuse(self) -> None:
        plan = self._plan((self.wheel,))
        original = CoreDurableResources.publish_bytes

        def fail_result(service, role, name, data, **kwargs):
            if name == "environment-wheel-import.json":
                raise RuntimeError("interrupted after managed-tree commit")
            return original(service, role, name, data, **kwargs)

        with patch.object(CoreDurableResources, "publish_bytes", fail_result):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan, (self.wheel,))
        self.wheel.unlink()
        restarted = self._plan()
        self.assertEqual("reuse", restarted["action"])
        self.assertIsNotNone(restarted["tree_id"])
        result = self._apply(restarted)
        self.assertEqual("reused", result["outcome"])
        reopened = reopen_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            result_resource_id=result["resource"]["resource_id"], environment=self.environment,
        )
        self.assertEqual(result["tree_id"], reopened["tree_id"])

    @skipIf(not sys.platform.startswith("linux"), "exact tree restart requires Linux")
    def test_hard_exit_before_tree_rename_reconciles_exact_stage(self) -> None:
        plan = self._plan((self.wheel,))

        def interrupted() -> None:
            from workbench_core import managed_trees
            with patch.object(managed_trees, "_rename_no_replace",
                              side_effect=lambda *args, **kwargs: os._exit(73)):
                self._apply(plan, (self.wheel,))

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(73, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        result = self._apply(restarted)
        self.assertEqual("reconciled", result["outcome"])
        reopened = reopen_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            result_resource_id=result["resource"]["resource_id"], environment=self.environment,
        )
        self.assertEqual(result["tree_id"], reopened["tree_id"])

    @skipIf(not sys.platform.startswith("linux"), "exact tree restart requires Linux")
    def test_changed_interrupted_stage_refuses_reconcile(self) -> None:
        plan = self._plan((self.wheel,))

        def interrupted() -> None:
            from workbench_core import managed_trees
            with patch.object(managed_trees, "_rename_no_replace",
                              side_effect=lambda *args, **kwargs: os._exit(73)):
                self._apply(plan, (self.wheel,))

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(73, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        from workbench_core.environment_wheel_import import _store
        from workbench_core.environment_resolution import resolve_environment
        local = resolve_environment(self.suite, workspace=self.workspace,
                                    environment=self.environment)
        host, _, _ = _store(local)
        row = next(row for row in host.catalog.trees.inventory(workspace=self.workspace)
                   if row["tree_id"] == restarted["tree_id"])
        staged_wheel = Path(row["staging"]) / self.wheel.name
        with staged_wheel.open("ab") as output:
            output.write(b"changed")
        with self.assertRaisesRegex(ReconstructionError, "cannot be reopened"):
            self._apply(restarted)
        self.assertFalse(Path(restarted["target"]).exists())

    @skipIf(not sys.platform.startswith("linux"), "exact tree restart requires Linux")
    def test_hard_exit_after_tree_rename_reconciles_commit(self) -> None:
        plan = self._plan((self.wheel,))

        def interrupted() -> None:
            from workbench_core.storage.tree_catalog import TreeCatalog
            original = TreeCatalog._write

            def exit_commit(catalog, section, *args, **kwargs):
                if section == "commits":
                    os._exit(74)
                return original(catalog, section, *args, **kwargs)

            with patch.object(TreeCatalog, "_write", exit_commit):
                self._apply(plan, (self.wheel,))

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(74, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        self.assertTrue(Path(restarted["target"]).is_dir())
        result = self._apply(restarted)
        self.assertEqual("reconciled", result["outcome"])

    def test_failed_stage_is_classified_and_can_retry_without_deletion(self) -> None:
        plan = self._plan((self.wheel,))
        from workbench_core import environment_wheel_import as importer
        original = importer._copy_sources

        def fail_copy(stage, sources, expected):
            original(stage, sources, expected)
            raise RuntimeError("interrupted before tree intent")

        with patch.object(importer, "_copy_sources", fail_copy):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan, (self.wheel,))
        retry = self._plan((self.wheel,))
        self.assertEqual("acquire", retry["action"])
        self.assertEqual(1, len(retry["prior_failed_trees"]))
        result = self._apply(retry, (self.wheel,))
        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(1, len(retry["prior_failed_trees"]))

    def test_share_candidate_mismatch_and_missing_wheels_block(self) -> None:
        plan = self._plan()
        self.assertEqual("blocked", plan["state"])
        self.assertEqual("local wheel sources are required for acquisition", plan["blockers"][0])
        other = deepcopy(self.candidate)
        other["share_id"] = "workbench-environment-share:sha256:" + "0" * 64
        with self.assertRaises(ReconstructionError):
            plan_wheel_import(
                self.suite, self.share, other, workspace=self.workspace,
                wheels=(self.wheel,), environment=self.environment,
            )


if __name__ == "__main__":
    import unittest
    unittest.main()
