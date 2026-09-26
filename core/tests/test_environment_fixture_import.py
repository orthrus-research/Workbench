"""Profile fixture source custody remains distinct from environment composition."""

from __future__ import annotations

from hashlib import sha256
import json
from multiprocessing import get_context
import os
from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch

from workbench_core.environment_fixture_import import (
    apply_fixture_import, plan_fixture_import, reopen_fixture_import,
)
from workbench_core.environment_input_candidates import build_input_candidate
from workbench_core.environment_reconstruction import ReconstructionError, _canonical
from workbench_core.output_routing import _private_directory
from workbench_core.storage.registered import CoreDurableResources

from . import test_environment_input_candidates as candidate_tests
from .test_environment_reconstruction import _environment


class _ImportFixtureOwner:
    def __init__(self, root: Path):
        self.root = root / "profiles/platforms/cleanroom/fixtures/demo"
        self.root.mkdir(parents=True)
        source = self.root / "src/main.txt"
        source.parent.mkdir()
        source.write_bytes(b"exact fixture source\n")
        row = {"path": "src/main.txt", "sha256": "sha256:" + sha256(source.read_bytes()).hexdigest(),
               "size": source.stat().st_size}
        digest = "sha256:" + sha256(_canonical({
            "algorithm": "sha256-file-tree-v1", "files": [row],
        })).hexdigest()
        self.lock = {
            "declaration_id": "fixture.demo",
            "declared_values": {"files": [row], "tree_digest": digest,
                                "tree_digest_algorithm": "sha256-file-tree-v1",
                                "identity": {"digest": digest}},
        }
        self.lock_path = self.root / "fixture-lock.json"
        self.lock_path.write_bytes(_canonical(self.lock) + b"\n")
        self.schema = root / "profiles/platforms/cleanroom/schemas/fixture-schema.json"
        self.schema.parent.mkdir(parents=True)
        self.schema.write_bytes(b'{"type":"object"}\n')
        self.tool = root / "profiles/platforms/cleanroom/tools/fixture-tool.py"
        self.tool.parent.mkdir(parents=True)
        self.tool.write_bytes(b"# exact preflight tool\n")
        self.inputs = tuple({
            "kind": kind, "path": path,
            "display_path": path.relative_to(root).as_posix(),
        } for kind, path in (
            ("fixture-owner-lock", self.lock_path),
            ("fixture-owner-schema", self.schema),
            ("profile-preflight-tool", self.tool),
        ))

    def read_owner_lock(self):
        return json.loads(self.lock_path.read_text(encoding="utf-8"))

    def validate_owner_lock(self, value):
        if value != self.lock or value != self.read_owner_lock():
            raise ValueError("owner lock changed")
        source = self.root / "src/main.txt"
        if source.is_symlink() or "sha256:" + sha256(source.read_bytes()).hexdigest() != self.lock["declared_values"]["files"][0]["sha256"]:
            raise ValueError("owner source changed")
        return value

    def fixture_root(self):
        return self.root

    def source_inputs(self):
        return self.inputs


@skipIf(not sys.platform.startswith("linux"), "fixture import is a Linux/WSL slice")
class EnvironmentFixtureImportTests(TestCase):
    def setUp(self) -> None:
        candidate_tests.EnvironmentInputCandidateTests.setUp(self)
        self.suite = self.root / "suite"
        self.workspace = self.root / "workspace"
        self.environment = _environment(self.root / "user")
        self.owner = _ImportFixtureOwner(self.root)
        for name in (
            "workbench_core.environment_input_candidates.require_profile_extension",
            "workbench_core.environment_fixture_import.require_profile_extension",
        ):
            replacement = patch(name, return_value=self.owner)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.candidate = build_input_candidate(
            self.share, wheels=(self.wheel,), profile_owner_id="cleanroom",
        )

    def _plan(self):
        return plan_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            environment=self.environment,
        )

    def _apply(self, plan):
        return apply_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            expected_plan_id=plan["plan_id"], environment=self.environment,
        )

    def _reopen(self, result):
        return reopen_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            result_resource_id=result["resource"]["resource_id"], environment=self.environment,
        )

    def test_acquire_reopen_and_reuse_after_source_disappears(self) -> None:
        plan = self._plan()
        self.assertEqual("acquire", plan["action"])
        with patch("workbench_core.module_cli._pip", side_effect=AssertionError("pip must not run")):
            result = self._apply(plan)
        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(4, len(result["files"]))
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        self.assertEqual(result["tree_id"], self._reopen(result)["tree_id"])
        for row in self.owner.inputs:
            row["path"].unlink()
        (self.owner.root / "src/main.txt").unlink()
        reuse = self._plan()
        self.assertEqual("reuse", reuse["action"])
        self.assertEqual(result["tree_id"], self._apply(reuse)["tree_id"])

    def test_changed_and_redirected_sources_refuse_acquisition(self) -> None:
        plan = self._plan()
        source = self.owner.root / "src/main.txt"
        source.write_bytes(b"changed\n")
        with self.assertRaises(ReconstructionError):
            self._apply(plan)
        with self.assertRaises(ReconstructionError):
            self._plan()
        source.write_bytes(b"exact fixture source\n")
        other = source.with_name("actual.txt")
        source.rename(other)
        source.symlink_to(other)
        with self.assertRaises(ReconstructionError):
            self._plan()

    def test_changed_schema_and_owner_identity_refuse_acquisition(self) -> None:
        plan = self._plan()
        self.owner.schema.write_bytes(b'{"type":"array"}\n')
        with self.assertRaises(ReconstructionError):
            self._apply(plan)
        self.owner.schema.write_bytes(b'{"type":"object"}\n')
        with patch("workbench_core.environment_input_candidates.profile_extension_identity",
                   return_value={**self.identity, "sha256": "0" * 64}):
            with self.assertRaisesRegex(ReconstructionError, "candidate"):
                self._plan()

    def test_parent_redirect_during_copy_is_refused_even_with_same_bytes(self) -> None:
        plan = self._plan()
        from workbench_core import environment_fixture_import as importer
        original = importer._copy_sources

        def redirect_parent(stage, sources):
            parent = self.owner.schema.parent
            relocated = parent.with_name("schemas-real")
            parent.rename(relocated)
            parent.symlink_to(relocated, target_is_directory=True)
            original(stage, sources)

        with patch.object(importer, "_copy_sources", redirect_parent):
            with self.assertRaisesRegex(ReconstructionError, "redirect"):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())

    def test_existing_target_and_changed_retained_bytes_refuse(self) -> None:
        plan = self._plan()
        target = Path(plan["target"])
        _private_directory(target.parent)
        target.mkdir(mode=0o700)
        marker = target / "foreign.txt"
        marker.write_text("foreign", encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "outside completed Core custody"):
            self._plan()
        marker.unlink()
        target.rmdir()
        result = self._apply(plan)
        retained = target / "profiles/platforms/cleanroom/fixtures/demo/src/main.txt"
        retained.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)
        with self.assertRaises(ReconstructionError):
            self._plan()

    def test_unsupported_host_is_blocked_before_copy(self) -> None:
        with patch("workbench_core.environment_fixture_import._tree_host_supported", return_value=False):
            plan = self._plan()
            self.assertEqual("blocked", plan["state"])
            with self.assertRaisesRegex(ReconstructionError, "blocked"):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())

    def test_public_parent_and_extra_stage_directory_refuse_publication(self) -> None:
        plan = self._plan()
        target_parent = Path(plan["target"]).parent
        _private_directory(target_parent)
        target_parent.chmod(0o755)
        with self.assertRaisesRegex(ReconstructionError, "owner-private"):
            self._plan()
        target_parent.chmod(0o700)
        from workbench_core import environment_fixture_import as importer
        original = importer._copy_sources

        def extra_directory(stage, sources):
            original(stage, sources)
            (stage / "extra-empty").mkdir()

        with patch.object(importer, "_copy_sources", extra_directory):
            with self.assertRaisesRegex(ReconstructionError, "extra members"):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())
        self.assertEqual("acquire", self._plan()["action"])

    def test_result_failure_after_commit_reuses_tree(self) -> None:
        plan = self._plan()
        original = CoreDurableResources.publish_bytes

        def fail_result(service, role, name, data, **kwargs):
            if name == "environment-fixture-import.json":
                raise RuntimeError("interrupted after tree commit")
            return original(service, role, name, data, **kwargs)

        with patch.object(CoreDurableResources, "publish_bytes", fail_result):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan)
        reused = self._plan()
        self.assertEqual("reuse", reused["action"])
        result = self._apply(reused)
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
        from workbench_core.environment_fixture_import import _store
        from workbench_core.environment_resolution import resolve_environment
        local = resolve_environment(self.suite, workspace=self.workspace,
                                    environment=self.environment)
        host, _, _ = _store(local)
        row = next(row for row in host.catalog.trees.inventory(workspace=self.workspace)
                   if row["tree_id"] == restarted["tree_id"])
        staged = Path(row["staging"]) / "profiles/platforms/cleanroom/fixtures/demo/src/main.txt"
        staged.write_bytes(b"changed")
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


if __name__ == "__main__":
    import unittest
    unittest.main()
